from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from icloud2nas.orchestrator.bus import EventBus
from icloud2nas.orchestrator.config import Config
from icloud2nas.orchestrator.onboarding import Onboarding
from icloud2nas.orchestrator.runs import RunManager
from icloud2nas.orchestrator.scheduler import DueJob, Scheduler, nightly_due
from icloud2nas.orchestrator.store import repo
from icloud2nas.orchestrator.store.db import Store, init_db, make_engine
from icloud2nas.orchestrator.store.models import Library
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
    init_db(engine)
    store = Store(engine)
    bus = EventBus()
    seen: list[tuple[str, dict[str, Any]]] = []
    bus.subscribe(lambda t, p: seen.append((t, p)))
    secrets = Secrets.from_config(config)
    runs = RunManager(config, store, secrets, bus)
    onboarding = Onboarding(config, store, secrets, bus, runs)
    clock = Clock(T0)
    sched = Scheduler(
        config, store, secrets, bus, runs, onboarding, clock=clock, jitter_s=(0, 0), tick_s=0.01
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
    return {"store": store, "sched": sched, "clock": clock, "seen": seen, **ids}


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
    sched.running_accounts.add(job_a.account_id)
    assert not sched.can_launch(job_a) and sched.can_launch(job_b)
    sched.running_libraries.update({"x", "y"})
    assert not sched.can_launch(job_b)  # max_workers=2


async def test_tick_runs_full_sync_and_nightly_chains_reconcile(
    world: dict[str, Any], config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, sched, clock = world["store"], world["sched"], world["clock"]
    _set(store, world["幻伊镜"], enabled=False)
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
    assert sched.running_libraries == set() and sched.running_accounts == set()


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
    world: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    store, sched = world["store"], world["sched"]
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
