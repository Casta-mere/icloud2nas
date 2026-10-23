from __future__ import annotations

import asyncio
import json
import os
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from icloud2nas.orchestrator.bus import EventBus
from icloud2nas.orchestrator.config import Config
from icloud2nas.orchestrator.onboarding import Onboarding
from icloud2nas.orchestrator.runs import RunManager
from icloud2nas.orchestrator.scheduler import DueJob, Scheduler, nightly_due
from icloud2nas.orchestrator.settings import Settings
from icloud2nas.orchestrator.store import repo
from icloud2nas.orchestrator.store.db import Store, make_engine, upgrade_db
from icloud2nas.orchestrator.store.models import Library, ReconcilePlan
from icloud2nas.orchestrator.store.secrets import Secrets

T0 = datetime(2026, 10, 16, 1, 0, tzinfo=UTC)  # 09:00 Shanghai


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def world(config: Config) -> dict[str, Any]:
    engine = make_engine(config.db_path)
    upgrade_db(engine)
    store = Store(engine)
    bus = EventBus()
    seen: list[tuple[str, dict[str, Any]]] = []
    bus.subscribe(lambda t, p: seen.append((t, p)))
    secrets = Secrets.from_config(config)
    runs = RunManager(config, store, secrets, bus)
    settings = Settings(store)
    onboarding = Onboarding(config, store, secrets, bus, runs, settings)
    clock = Clock(T0)
    sched = Scheduler(
        config,
        store,
        secrets,
        bus,
        runs,
        onboarding,
        settings,
        clock=clock,
        jitter_s=(0, 0),
        tick_s=0.01,
    )
    ids: dict[str, str] = {}
    with store.session() as s:
        for name, apple_id in (("旭刚", "a@icloud.com"), ("幻伊镜", "b@icloud.com")):
            acc = repo.create_account(
                s,
                display_name=name,
                apple_id=apple_id,
                region="cn",
                password_enc=secrets.encrypt("pw"),
                client_id="11111111-1111-1111-1111-111111111111",
            )
            acc.status = "ok"
            lib = repo.upsert_library(
                s,
                acc,
                kind="personal",
                zone_name="PrimarySync",
                display_name=name,
                target_dir=name,
                interval_min=30,
                nightly_at="03:00",
            )
            lib.enabled = True
            ids[name] = lib.id
            ids[name + "_acc"] = acc.id
    return {
        "store": store,
        "settings": settings,
        "sched": sched,
        "clock": clock,
        "seen": seen,
        **ids,
    }


def _set(store: Store, library_id: str, **fields: Any) -> None:
    with store.session() as s:
        lib = repo.get_library(s, library_id)
        assert lib is not None
        for k, v in fields.items():
            setattr(lib, k, v)


def test_never_synced_library_is_due_for_a_full_sync(world: dict[str, Any]) -> None:
    jobs = world["sched"].due_jobs()
    assert {(j.library_id, j.kind, j.trigger) for j in jobs} == {
        (world["旭刚"], "full", "schedule"),
        (world["幻伊镜"], "full", "schedule"),
    }


def test_interval_quick_and_nightly_rules(world: dict[str, Any]) -> None:
    store, sched, clock = world["store"], world["sched"], world["clock"]
    _set(
        store, world["旭刚"], last_success_at=T0 - timedelta(minutes=10), last_nightly_on=T0.date()
    )
    _set(
        store,
        world["幻伊镜"],
        last_success_at=T0 - timedelta(minutes=31),
        last_nightly_on=T0.date(),
    )
    jobs = world["sched"].due_jobs()
    assert [(j.library_id, j.kind) for j in jobs] == [(world["幻伊镜"], "quick")]
    # 03:05 Shanghai next day, nightly not yet done -> nightly even if interval not reached
    clock.now = datetime(2026, 10, 16, 19, 5, tzinfo=UTC)
    _set(store, world["旭刚"], last_success_at=clock.now - timedelta(minutes=1))
    jobs = sched.due_jobs()
    assert (world["旭刚"], "nightly", "nightly") in {
        (j.library_id, j.kind, j.trigger) for j in jobs
    }
    # a nightly missed for several days runs once, not once per missed day
    _set(store, world["旭刚"], last_nightly_on=T0.date() - timedelta(days=5))
    assert sum(1 for j in sched.due_jobs() if j.library_id == world["旭刚"]) == 1


def test_nightly_due_helper(world: dict[str, Any]) -> None:
    lib = Library(nightly_at="03:00", last_nightly_on=None)
    assert nightly_due(lib, datetime(2026, 10, 17, 3, 0)) is True
    assert nightly_due(lib, datetime(2026, 10, 17, 2, 59)) is False
    lib.last_nightly_on = datetime(2026, 10, 17).date()
    assert nightly_due(lib, datetime(2026, 10, 17, 23, 0)) is False


def test_skips_disabled_shared_backoff_reauth_and_paused(world: dict[str, Any]) -> None:
    store, sched = world["store"], world["sched"]
    with store.session() as s:
        acc = repo.get_account(s, world["幻伊镜_acc"])
        assert acc is not None
        acc.status = "needs_reauth"
    _set(store, world["旭刚"], enabled=False)
    assert sched.due_jobs() == []
    _set(store, world["旭刚"], enabled=True, status="paused")
    assert sched.due_jobs() == []
    _set(store, world["旭刚"], status="ok")
    with store.session() as s:
        acc = repo.get_account(s, world["旭刚_acc"])
        assert acc is not None
        acc.backoff_until = T0 + timedelta(minutes=5)
    assert sched.due_jobs() == []
    with store.session() as s:
        acc = repo.get_account(s, world["旭刚_acc"])
        assert acc is not None
        acc.backoff_until = T0 - timedelta(minutes=5)
        repo.set_setting(s, "paused", "1")
    assert sched.due_jobs() == []
    with store.session() as s:
        repo.set_setting(s, "paused", "0")
    assert [j.library_id for j in sched.due_jobs()] == [world["旭刚"]]


def test_locks_and_concurrency_cap(world: dict[str, Any]) -> None:
    sched = world["sched"]
    job_a = DueJob(world["旭刚"], world["旭刚_acc"], "full", "schedule")
    job_b = DueJob(world["幻伊镜"], world["幻伊镜_acc"], "full", "schedule")
    assert sched.can_launch(job_a) and sched.can_launch(job_b)
    sched.running_libraries.add(job_a.library_id)
    assert sched.hold_account(job_a.account_id, "run-a")
    assert not sched.can_launch(job_a) and sched.can_launch(job_b)
    sched.running_libraries.update({"x", "y"})
    assert not sched.can_launch(job_b)  # max_workers=2


async def test_cap_comes_from_settings(
    world: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(
        "FAKE_WORKER_SCENARIO",
        json.dumps(
            {
                "sync": {
                    "events": [{"ev": "started"}, {"sleep": 0.3}, {"ev": "done", "stats": {}}],
                    "exit": 0,
                }
            }
        ),
    )
    sched = world["sched"]
    world["settings"].set("max_workers", "1")
    assert len(sched.due_jobs()) == 2
    assert await sched.tick() == 1  # the second account's due job waits for a free slot
    await asyncio.gather(*sched.tasks)


def test_paused_reads_settings(world: dict[str, Any]) -> None:
    world["settings"].set("paused", "true")
    assert world["sched"].paused() and world["sched"].due_jobs() == []


async def test_tick_runs_full_sync_and_nightly_chains_reconcile(
    world: dict[str, Any], config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, sched, clock = world["store"], world["sched"], world["clock"]
    _set(store, world["幻伊镜"], enabled=False)
    (config.photos_dir / "旭刚").mkdir(parents=True)  # the fake worker writes nothing
    launched = await sched.tick()
    assert launched == 1
    await asyncio.gather(*sched.tasks)
    with store.session() as s:
        runs = repo.list_runs(s, library_id=world["旭刚"])
        assert [r.mode for r in runs] == ["sync"] and runs[0].status == "ok"
        lib = repo.get_library(s, world["旭刚"])
        assert lib is not None and lib.last_success_at is not None
    clock.now = datetime(2026, 10, 16, 19, 5, tzinfo=UTC)  # 03:05 local next day
    assert await sched.tick() == 1
    await asyncio.gather(*sched.tasks)
    with store.session() as s:
        runs = repo.list_runs(s, library_id=world["旭刚"])
        assert [(r.mode, r.trigger, r.status) for r in runs] == [
            ("reconcile", "chain", "ok"),
            ("sync", "nightly", "ok"),
            ("sync", "schedule", "ok"),
        ]
        lib = repo.get_library(s, world["旭刚"])
        assert lib is not None and lib.last_nightly_on == datetime(2026, 10, 17).date()
    assert sched.running_libraries == set() and sched.active == {}


async def test_manual_request_and_busy(
    world: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    sched = world["sched"]
    monkeypatch.setenv(
        "FAKE_WORKER_SCENARIO",
        json.dumps(
            {
                "sync": {
                    "events": [{"ev": "started"}, {"sleep": 1}, {"ev": "done", "stats": {}}],
                    "exit": 0,
                }
            }
        ),
    )
    job = sched.request_manual(world["旭刚"], "sync")
    assert job.kind == "full" and job.trigger == "manual"
    run_id = await sched.launch(job)
    await asyncio.sleep(0.2)
    assert run_id in sched.active
    with pytest.raises(RuntimeError, match="busy"):
        await sched.launch(sched.request_manual(world["旭刚"], "reconcile"))
    await asyncio.gather(*sched.tasks)
    assert sched.active == {}


async def test_shutdown_kills_running_worker_and_marks_it_killed(
    world: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    store, sched = world["store"], world["sched"]
    monkeypatch.setenv(
        "FAKE_WORKER_SCENARIO",
        json.dumps({"sync": {"events": [{"ev": "started"}, {"sleep": 30}], "exit": 0}}),
    )
    _set(store, world["幻伊镜"], enabled=False)
    stop = asyncio.Event()
    daemon = asyncio.create_task(sched.run_forever(stop))
    for _ in range(50):
        await asyncio.sleep(0.05)
        if sched.active:
            break
    assert sched.active
    stop.set()
    await asyncio.wait_for(daemon, 10)
    with store.session() as s:
        run = repo.list_runs(s, library_id=world["旭刚"])[0]
        assert run.status == "failed" and run.error_kind == "killed"
        acc = repo.get_account(s, world["旭刚_acc"])
        assert acc is not None and acc.failures == 0 and acc.backoff_until is None
    assert sched.active == {} and sched.running_libraries == set()


async def test_startup_sweeps_stale_runs_and_space_pause(
    world: dict[str, Any], config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, sched = world["store"], world["sched"]
    with store.session() as s:
        stale = repo.create_run(
            s,
            account_id=world["旭刚_acc"],
            library_id=world["旭刚"],
            mode="sync",
            trigger="schedule",
        )
        stale.status = "running"
    monkeypatch.setattr(sched, "free_space_ok", lambda: False)
    stop = asyncio.Event()
    daemon = asyncio.create_task(sched.run_forever(stop))
    await asyncio.sleep(0.1)
    stop.set()
    await asyncio.wait_for(daemon, 5)
    with store.session() as s:
        assert repo.get_run(s, stale.id).status == "failed"  # type: ignore[union-attr]
        assert all(lib.status == "space_paused" for lib in repo.list_libraries(s))
    assert any(t == "space.low" for t, _ in world["seen"])
    monkeypatch.setattr(sched, "free_space_ok", lambda: True)
    assert [j.kind for j in sched.due_jobs()] == ["full", "full"]
    with store.session() as s:
        assert all(lib.status == "ok" for lib in repo.list_libraries(s))


async def test_cap_is_enforced_within_a_single_tick(
    world: dict[str, Any], config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, sched = world["store"], world["sched"]
    with store.session() as s:
        acc = repo.create_account(
            s,
            display_name="third",
            apple_id="c@icloud.com",
            region="cn",
            password_enc=Secrets.from_config(config).encrypt("pw"),
            client_id="11111111-1111-1111-1111-111111111111",
        )
        acc.status = "ok"
        lib = repo.upsert_library(
            s,
            acc,
            kind="personal",
            zone_name="PrimarySync",
            display_name="third",
            target_dir="third",
            interval_min=30,
            nightly_at="03:00",
        )
        lib.enabled = True
    monkeypatch.setenv(
        "FAKE_WORKER_SCENARIO",
        json.dumps(
            {
                "sync": {
                    "events": [{"ev": "started"}, {"sleep": 0.5}, {"ev": "done", "stats": {}}],
                    "exit": 0,
                }
            }
        ),
    )
    assert len(sched.due_jobs()) == 3
    assert await sched.tick() == 2  # max_workers=2
    await asyncio.gather(*sched.tasks)
    assert await sched.tick() == 1
    await asyncio.gather(*sched.tasks)


async def test_crash_before_execute_finishes_run_and_releases_locks(
    world: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    store, sched = world["store"], world["sched"]
    _set(store, world["幻伊镜"], enabled=False)

    def boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("bad ciphertext")

    monkeypatch.setattr(sched.onboarding, "password_for", boom)
    assert await sched.tick() == 1
    await asyncio.gather(*sched.tasks)
    with store.session() as s:
        run = repo.list_runs(s, library_id=world["旭刚"])[0]
        assert run.status == "failed" and run.error_kind == "internal"
    assert sched.running_libraries == set() and sched.running_accounts == {}


async def test_first_full_sync_stamps_tonight_and_nightly_uses_start_date(
    world: dict[str, Any],
) -> None:
    store, sched, clock = world["store"], world["sched"], world["clock"]
    _set(store, world["幻伊镜"], enabled=False)
    clock.now = datetime(2026, 10, 16, 19, 5, tzinfo=UTC)  # 03:05 local on Oct 17, past nightly_at
    assert await sched.tick() == 1  # first-ever full sync
    await asyncio.gather(*sched.tasks)
    with store.session() as s:
        lib = repo.get_library(s, world["旭刚"])
        assert lib is not None and lib.last_nightly_on == datetime(2026, 10, 17).date()
    # no redundant nightly the same local day (a quick is fine: the fake clock is hours ahead)
    assert all(j.kind != "nightly" for j in sched.due_jobs())


async def test_space_paused_libraries_recover_after_restart(
    world: dict[str, Any], config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, sched = world["store"], world["sched"]
    for name in ("旭刚", "幻伊镜"):
        (config.photos_dir / name).mkdir(parents=True)  # the fake worker writes nothing
    with store.session() as s:
        for lib in repo.list_libraries(s):
            lib.status = "space_paused"  # left over from a previous daemon
    monkeypatch.setattr(sched, "free_space_ok", lambda: True)
    stop = asyncio.Event()
    daemon = asyncio.create_task(sched.run_forever(stop))
    await asyncio.sleep(0.1)
    stop.set()
    await asyncio.wait_for(daemon, 10)
    with store.session() as s:
        assert all(lib.status == "ok" for lib in repo.list_libraries(s))


async def test_manual_full_sync_does_not_stamp_nightly(world: dict[str, Any]) -> None:
    store, sched = world["store"], world["sched"]
    await sched.launch(sched.request_manual(world["旭刚"], "sync"))
    await asyncio.gather(*sched.tasks)
    with store.session() as s:
        lib = repo.get_library(s, world["旭刚"])
        assert lib is not None and lib.last_success_at is not None
        assert lib.last_nightly_on is None


def _scenario(monkeypatch: pytest.MonkeyPatch, mode: str, exit_code: int) -> None:
    monkeypatch.setenv(
        "FAKE_WORKER_SCENARIO",
        json.dumps(
            {mode: {"events": [{"ev": "started"}, {"ev": "done", "stats": {}}], "exit": exit_code}}
        ),
    )


def _approved_plan(world: dict[str, Any], config: Config, status: str = "approved") -> str:
    plan_file = config.plans_dir / "plan.json"
    plan_file.parent.mkdir(parents=True, exist_ok=True)
    plan_file.write_text("{}")
    with world["store"].session() as s:
        run = repo.create_run(
            s,
            account_id=world["旭刚_acc"],
            library_id=world["旭刚"],
            mode="reconcile",
            trigger="manual",
        )
        run.status = "ok"
        plan = repo.create_plan(
            s,
            library_id=world["旭刚"],
            run_id=run.id,
            extraneous_count=1,
            plan_file=str(plan_file),
        )
        plan.status = status
        return str(plan.id)


async def test_manual_job_runs_before_due_jobs(
    world: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    sched = world["sched"]
    monkeypatch.setattr(sched.settings, "get", lambda k: 1 if k == "max_workers" else 0)
    _scenario(monkeypatch, "sync", 0)
    job = sched.request_manual(world["幻伊镜"], "sync")
    sched.enqueue(job)
    assert sched.pending_manual() == [job]
    assert await sched.tick() == 1
    assert sched.running_libraries == {world["幻伊镜"]}
    assert sched.pending_manual() == []
    await asyncio.gather(*sched.tasks)


async def test_manual_queue_dedupes(world: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    sched = world["sched"]
    _scenario(monkeypatch, "sync", 0)
    sched.enqueue(sched.request_manual(world["幻伊镜"], "sync"))
    sched.enqueue(sched.request_manual(world["幻伊镜"], "sync"))
    assert len(sched.pending_manual()) == 1
    _set(world["store"], world["旭刚"], enabled=False)
    await sched.tick()
    await asyncio.gather(*sched.tasks)
    with world["store"].session() as s:
        assert len(repo.list_runs(s, library_id=world["幻伊镜"])) == 1


async def test_manual_ignores_pause_but_not_account_state(
    world: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    store, sched = world["store"], world["sched"]
    _scenario(monkeypatch, "sync", 0)
    world["settings"].set("paused", "true")
    _set(store, world["旭刚"], enabled=False)
    sched.enqueue(sched.request_manual(world["幻伊镜"], "sync"))
    assert await sched.tick() == 1
    await asyncio.gather(*sched.tasks)
    with store.session() as s:
        assert [r.status for r in repo.list_runs(s, library_id=world["幻伊镜"])] == ["ok"]
        acc = repo.get_account(s, world["旭刚_acc"])
        assert acc is not None
        acc.status = "needs_reauth"
    _set(store, world["旭刚"], enabled=True)
    with pytest.raises(ValueError):
        sched.enqueue(sched.request_manual(world["旭刚"], "sync"))
    _set(store, world["幻伊镜"], enabled=False)
    with pytest.raises(ValueError):
        sched.enqueue(sched.request_manual(world["幻伊镜"], "sync"))
    with store.session() as s:
        acc = repo.get_account(s, world["旭刚_acc"])
        assert acc is not None
        acc.status = "ok"
    monkeypatch.setattr(sched, "free_space_ok", lambda: False)
    with pytest.raises(RuntimeError, match="space low"):
        sched.enqueue(sched.request_manual(world["旭刚"], "sync"))


async def test_apply_plan_marks_executed_and_publishes_resolved(
    world: dict[str, Any], config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    sched = world["sched"]
    _scenario(monkeypatch, "reconcile", 0)
    plan_id = _approved_plan(world, config)
    sched.enqueue(DueJob(world["旭刚"], world["旭刚_acc"], "apply_plan", "manual", plan_id=plan_id))
    await sched.tick()
    await sched.wait_idle(world["旭刚"])
    await asyncio.gather(*sched.tasks)
    with world["store"].session() as s:
        plan = s.get(ReconcilePlan, plan_id)
        assert plan is not None and plan.status == "executed"
        runs = repo.list_runs(s, library_id=world["旭刚"])
        assert [(r.mode, r.trigger) for r in runs if r.id != plan.run_id] == [
            ("reconcile", "manual")
        ]
    resolved = [p for t, p in world["seen"] if t == "plan.resolved"]
    assert resolved == [{"plan_id": plan_id, "library_id": world["旭刚"], "status": "executed"}]


async def test_apply_plan_failure_returns_plan_to_pending(
    world: dict[str, Any], config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    sched = world["sched"]
    _scenario(monkeypatch, "reconcile", 1)
    plan_id = _approved_plan(world, config)
    sched.enqueue(DueJob(world["旭刚"], world["旭刚_acc"], "apply_plan", "manual", plan_id=plan_id))
    await sched.tick()
    await asyncio.gather(*sched.tasks)
    with world["store"].session() as s:
        plan = s.get(ReconcilePlan, plan_id)
        assert plan is not None and plan.status == "pending"
    resolved = [p for t, p in world["seen"] if t == "plan.resolved"]
    assert [p["status"] for p in resolved] == ["pending"]


async def test_apply_plan_no_longer_approved_is_refused(
    world: dict[str, Any], config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    sched = world["sched"]
    _scenario(monkeypatch, "reconcile", 0)
    plan_id = _approved_plan(world, config, status="rejected")
    sched.enqueue(DueJob(world["旭刚"], world["旭刚_acc"], "apply_plan", "manual", plan_id=plan_id))
    await sched.tick()
    await asyncio.gather(*sched.tasks)
    resolved = [p for t, p in world["seen"] if t == "plan.resolved"]
    assert [p["status"] for p in resolved] == ["rejected"]
    with world["store"].session() as s:
        assert [r.mode for r in repo.list_runs(s, library_id=world["旭刚"])] == ["reconcile"]


async def test_startup_sweeps_approved_plans_and_running_runs(
    world: dict[str, Any], config: Config
) -> None:
    store, sched = world["store"], world["sched"]
    plan_id = _approved_plan(world, config)
    with store.session() as s:
        stale = repo.create_run(
            s,
            account_id=world["旭刚_acc"],
            library_id=world["旭刚"],
            mode="sync",
            trigger="schedule",
        )
        stale.status = "running"
    world["settings"].set("paused", "true")
    stop = asyncio.Event()
    stop.set()
    await asyncio.wait_for(sched.run_forever(stop), 5)
    with store.session() as s:
        plan = s.get(ReconcilePlan, plan_id)
        assert plan is not None and plan.status == "pending"
        run = repo.get_run(s, stale.id)
        assert run is not None and run.status == "failed" and run.error_kind == "killed"


async def test_enqueue_wakes_the_loop_before_the_next_tick(world: dict[str, Any]) -> None:
    store, sched, clock = world["store"], world["sched"], world["clock"]
    for name in ("旭刚", "幻伊镜"):  # nothing due: only the manual job can start a run
        _set(store, world[name], last_success_at=clock.now, last_nightly_on=clock.now.date())
    sched.tick_s = 10
    stop = asyncio.Event()
    daemon = asyncio.create_task(sched.run_forever(stop))
    await asyncio.sleep(0.1)  # first tick done, the loop is now parked for up to 10 s
    with store.session() as s:
        assert repo.list_runs(s, library_id=world["旭刚"]) == []
    sched.enqueue(sched.request_manual(world["旭刚"], "sync"))
    deadline = asyncio.get_running_loop().time() + 0.5
    started = False
    while not started and asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(0.02)
        with store.session() as s:
            started = bool(repo.list_runs(s, library_id=world["旭刚"]))
    stop.set()
    await asyncio.wait_for(daemon, 10)
    assert started


def test_account_holds_are_per_owner(world: dict[str, Any]) -> None:
    sched, acc = world["sched"], world["旭刚_acc"]
    assert sched.hold_account(acc, "run-1")
    assert sched.hold_account(acc, "run-1")  # the holder may re-assert its own hold
    assert not sched.hold_account(acc, "discovery:" + acc)
    sched.release_account(acc, "discovery:" + acc)  # not the holder: no-op
    assert sched.running_accounts == {acc: "run-1"}
    sched.release_account(acc, "run-1")
    assert sched.running_accounts == {}


def test_can_launch_refuses_an_account_in_an_auth_session(world: dict[str, Any]) -> None:
    sched = world["sched"]
    job = DueJob(world["旭刚"], world["旭刚_acc"], "full", "schedule")
    sched.onboarding.sessions[world["旭刚_acc"]] = object()
    assert not sched.can_launch(job)
    assert [j.library_id for j in sched.due_jobs() if sched.can_launch(j)] == [world["幻伊镜"]]


async def test_tick_drops_queued_job_of_deleted_account_and_continues(
    world: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    store, sched = world["store"], world["sched"]
    _scenario(monkeypatch, "sync", 0)
    sched.enqueue(sched.request_manual(world["旭刚"], "sync"))
    sched.enqueue(sched.request_manual(world["幻伊镜"], "sync"))
    with store.session() as s:
        repo.delete_account_cascade(s, world["旭刚_acc"])
    world["settings"].set("paused", "1")  # only the queue matters here
    assert await sched.tick() == 1
    await asyncio.gather(*sched.tasks)
    assert sched.pending_manual() == []
    with store.session() as s:
        assert [r.status for r in repo.list_runs(s, library_id=world["幻伊镜"])] == ["ok"]


async def test_tick_drops_a_job_whose_launch_raises_and_continues(
    world: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    store, sched = world["store"], world["sched"]
    _scenario(monkeypatch, "sync", 0)
    sched.enqueue(sched.request_manual(world["旭刚"], "sync"))
    sched.enqueue(sched.request_manual(world["幻伊镜"], "sync"))
    real = sched.runs.start_run

    def flaky(**kwargs: Any) -> str:
        if kwargs["library_id"] == world["旭刚"]:
            raise RuntimeError("database is locked")
        return real(**kwargs)

    monkeypatch.setattr(sched.runs, "start_run", flaky)
    world["settings"].set("paused", "1")
    assert await sched.tick() == 1
    await asyncio.gather(*sched.tasks)
    assert sched.pending_manual() == [] and sched.running_accounts == {}
    with store.session() as s:
        assert len(repo.list_runs(s, library_id=world["幻伊镜"])) == 1


async def test_queued_job_is_dropped_when_its_account_needs_reauth(
    world: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    store, sched = world["store"], world["sched"]
    _scenario(monkeypatch, "sync", 0)
    sched.enqueue(sched.request_manual(world["旭刚"], "sync"))
    with store.session() as s:
        acc = repo.get_account(s, world["旭刚_acc"])
        assert acc is not None
        acc.status = "needs_reauth"
    world["settings"].set("paused", "1")
    assert await sched.tick() == 0
    assert sched.pending_manual() == []
    with store.session() as s:
        assert repo.list_runs(s, library_id=world["旭刚"]) == []


async def test_dropped_apply_plan_job_returns_the_plan_to_pending(
    world: dict[str, Any], config: Config
) -> None:
    store, sched = world["store"], world["sched"]
    plan_id = _approved_plan(world, config)
    sched.enqueue(DueJob(world["旭刚"], world["旭刚_acc"], "apply_plan", "manual", plan_id=plan_id))
    assert sched.drop_manual(account_id=world["旭刚_acc"]) == 1
    with store.session() as s:
        plan = s.get(ReconcilePlan, plan_id)
        assert plan is not None and plan.status == "pending"
    assert ("plan.resolved", {"plan_id": plan_id, "library_id": world["旭刚"],
                              "status": "pending"}) in world["seen"]


async def test_manual_request_during_startup_jitter_runs_at_once(
    world: dict[str, Any],
) -> None:
    store, sched = world["store"], world["sched"]
    sched.jitter_s = (5, 5)
    stop = asyncio.Event()
    daemon = asyncio.create_task(sched.run_forever(stop))
    await asyncio.sleep(0.05)
    sched.enqueue(sched.request_manual(world["旭刚"], "sync"))
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 0.5
    started = False
    while not started and loop.time() < deadline:
        await asyncio.sleep(0.02)
        with store.session() as s:
            started = bool(repo.list_runs(s, library_id=world["旭刚"]))
    await asyncio.sleep(0.2)
    with store.session() as s:  # 幻伊镜 is due (never synced) but must wait out the jitter
        due_started = bool(repo.list_runs(s, library_id=world["幻伊镜"]))
    stop.set()
    await asyncio.wait_for(daemon, 10)
    assert started and not due_started


async def test_shutdown_kills_workers_concurrently(
    world: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    sched = world["sched"]
    monkeypatch.setenv(
        "FAKE_WORKER_SCENARIO",
        json.dumps(
            {"sync": {"events": [{"ignore_term": 1.0}, {"ev": "started"}, {"sleep": 30}],
                      "exit": 0}}
        ),
    )
    assert await sched.tick() == 2
    for _ in range(100):
        await asyncio.sleep(0.05)
        if len(sched.active) == 2:
            break
    assert len(sched.active) == 2
    await asyncio.sleep(0.2)  # both workers are past their SIGTERM handler install
    began = time.monotonic()
    await sched.shutdown()
    elapsed = time.monotonic() - began
    assert sched.active == {} and sched.running_accounts == {}
    assert elapsed < 1.9, elapsed  # one shared grace period, not one per worker


async def test_shutdown_kills_interactive_auth_sessions(
    world: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    store, sched = world["store"], world["sched"]
    monkeypatch.setenv(
        "FAKE_WORKER_SCENARIO",
        json.dumps({"auth": {"events": [{"ev": "started"}, {"wait_code": "123456"}], "exit": 0}}),
    )
    session = await sched.onboarding.start_auth(world["旭刚_acc"])
    await asyncio.sleep(0.2)
    await asyncio.wait_for(sched.shutdown(), 3)
    assert session.finished.is_set() and world["旭刚_acc"] not in sched.onboarding.sessions
    with store.session() as s:
        run = repo.get_run(s, session.run_id)
        assert run is not None and run.status == "failed" and run.error_kind == "killed"


def _synced(world: dict[str, Any], config: Config, name: str = "旭刚", **fields: Any) -> Any:
    folder = config.photos_dir / name
    _set(
        world["store"],
        world[name],
        first_full_at=T0 - timedelta(days=3),
        last_success_at=T0 - timedelta(hours=2),
        last_nightly_on=T0.date(),
        **fields,
    )
    return folder


def _status(world: dict[str, Any], name: str = "旭刚") -> tuple[str, dict[str, Any]]:
    with world["store"].session() as s:
        lib = repo.get_library(s, world[name])
        return lib.status, lib.stats


async def test_synced_library_with_missing_folder_goes_dir_missing_and_never_runs(
    world: dict[str, Any], config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    sched, store = world["sched"], world["store"]
    _set(store, world["幻伊镜"], enabled=False)
    folder = _synced(world, config)
    assert not folder.exists()
    assert sched.due_jobs() == []
    status, stats = _status(world)
    assert status == "dir_missing" and stats["status_before_missing"] == "ok"
    assert ("library.updated", {"library_id": world["旭刚"]}) in world["seen"]
    assert await sched.tick() == 0
    assert not sched.can_launch(DueJob(world["旭刚"], world["旭刚_acc"], "quick", "schedule"))
    with store.session() as s:
        assert repo.list_runs(s, library_id=world["旭刚"]) == []
    assert not folder.exists()


async def test_run_one_rechecks_before_prepare_spawn_and_creates_no_folder(
    world: dict[str, Any], config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    sched, store = world["sched"], world["store"]
    folder = _synced(world, config)
    folder.mkdir(parents=True)
    job = DueJob(world["旭刚"], world["旭刚_acc"], "quick", "schedule")
    run_id = await sched.launch(job)  # the folder vanishes between enqueue and launch
    folder.rmdir()
    called: list[object] = []
    monkeypatch.setattr(
        "icloud2nas.orchestrator.scheduler.owners.prepare_spawn",
        lambda *a, **k: called.append(a),
    )
    await asyncio.gather(*sched.tasks)
    assert called == [] and not folder.exists()
    with store.session() as s:
        run = repo.get_run(s, run_id)
        assert run.status == "failed" and run.error_kind == "dir_missing"
        assert repo.get_account(s, world["旭刚_acc"]).failures == 0
    assert _status(world)[0] == "dir_missing"
    assert sched.running_libraries == set() and sched.running_accounts == {}


def test_folder_back_restores_status(world: dict[str, Any], config: Config) -> None:
    sched = world["sched"]
    _set(world["store"], world["幻伊镜"], enabled=False)
    folder = _synced(world, config, status="paused")
    sched.due_jobs()
    assert _status(world)[0] == "dir_missing"
    assert _status(world)[1]["status_before_missing"] == "paused"
    folder.mkdir(parents=True)
    assert sched.due_jobs() == []  # paused again, so still nothing due
    status, stats = _status(world)
    assert status == "paused" and "status_before_missing" not in stats
    _set(world["store"], world["旭刚"], status="ok")
    folder.rmdir()
    sched.due_jobs()
    folder.mkdir()
    assert [j.library_id for j in sched.due_jobs()] == [world["旭刚"]]
    assert _status(world)[0] == "ok"


def test_space_paused_library_is_left_alone(world: dict[str, Any], config: Config) -> None:
    _synced(world, config, status="space_paused")
    world["sched"]._space_low = True
    assert world["sched"]._apply_dir_state() is False
    assert _status(world)[0] == "space_paused"


def test_never_synced_library_still_runs(world: dict[str, Any], config: Config) -> None:
    jobs = world["sched"].due_jobs()
    assert {j.library_id for j in jobs} == {world["旭刚"], world["幻伊镜"]}
    assert not (config.photos_dir / "旭刚").exists()


def test_manual_sync_on_dir_missing_is_409_busy(world: dict[str, Any], config: Config) -> None:
    from icloud2nas.orchestrator.ops import Busy

    _synced(world, config)
    with pytest.raises(Busy, match="library folder is missing"):
        world["sched"].enqueue(world["sched"].request_manual(world["旭刚"], "sync"))
    assert world["sched"].pending_manual() == []


async def test_folder_vanishing_after_the_check_fails_the_run_without_recreating_it(
    world: dict[str, Any], config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    sched, store = world["sched"], world["store"]
    folder = _synced(world, config)
    folder.mkdir(parents=True)
    real = sched._dir_vanished
    checks: list[bool] = []

    def vanish_after_check(library_id: str) -> bool:
        result = real(library_id)
        checks.append(result)
        if folder.exists():
            folder.rmdir()  # gone between the dir_missing check and prepare_spawn
        return result

    monkeypatch.setattr(sched, "_dir_vanished", vanish_after_check)
    run_id = await sched.launch(DueJob(world["旭刚"], world["旭刚_acc"], "quick", "schedule"))
    await asyncio.gather(*sched.tasks)
    assert checks[0] is False and not folder.exists()
    with store.session() as s:
        run = repo.get_run(s, run_id)
        assert run.status == "failed" and run.error_kind == "dir_missing"
        assert repo.get_account(s, world["旭刚_acc"]).failures == 0
    assert _status(world)[0] == "dir_missing"


async def test_a_store_symlinked_to_another_library_fails_the_run_before_any_chown(
    world: dict[str, Any], config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    from icloud2nas.orchestrator import owners

    sched, store = world["sched"], world["store"]
    with store.session() as s:
        account = repo.get_account(s, world["旭刚_acc"])
        account.owner_uid, account.owner_gid = 1000, 10
    monkeypatch.setattr(owners, "privileged", lambda: True)
    chowned: list[str] = []  # real chowns would need root; record what they would touch
    monkeypatch.setattr(owners.os, "chown", lambda p, *a, **k: chowned.append(str(p)))
    monkeypatch.setattr(
        owners.os, "fchown", lambda fd, *a: chowned.append(os.readlink(f"/proc/self/fd/{fd}"))
    )
    other = config.photos_dir / "幻伊镜" / ".icloud2nas"
    (other / "trash").mkdir(parents=True)
    folder = _synced(world, config)
    folder.mkdir(parents=True)
    (folder / ".icloud2nas").symlink_to(other)
    run_id = await sched.launch(DueJob(world["旭刚"], world["旭刚_acc"], "quick", "schedule"))
    await asyncio.gather(*sched.tasks, return_exceptions=True)
    with store.session() as s:
        run = repo.get_run(s, run_id)
        assert run.status == "failed" and run.error_kind == "internal"
        assert "symlink" in (run.error_message or "")
    assert not any(p.startswith(str(other.parent)) or p.startswith(str(folder)) for p in chowned)
