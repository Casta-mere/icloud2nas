from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest

from icloud2nas.orchestrator.bus import EventBus
from icloud2nas.orchestrator.config import Config
from icloud2nas.orchestrator.jobs import build_job
from icloud2nas.orchestrator.runs import RunManager
from icloud2nas.orchestrator.store import repo
from icloud2nas.orchestrator.store.db import Store, init_db, make_engine
from icloud2nas.orchestrator.store.secrets import Secrets
from icloud2nas.orchestrator.supervisor import WorkerResult


@pytest.fixture
def world(config: Config) -> dict[str, Any]:
    engine = make_engine(config.db_path)
    init_db(engine)
    store = Store(engine)
    bus = EventBus()
    seen: list[tuple[str, dict[str, Any]]] = []
    bus.subscribe(lambda t, p: seen.append((t, p)))
    with store.session() as s:
        acc = repo.create_account(
            s, display_name="旭刚", apple_id="a@icloud.com", region="cn",
            password_enc=Secrets.from_config(config).encrypt("pw"),
            client_id="11111111-1111-1111-1111-111111111111",
        )
        acc.status = "ok"
        lib = repo.upsert_library(
            s, acc, kind="personal", zone_name="PrimarySync", display_name="旭刚",
            target_dir="旭刚", interval_min=30, nightly_at="03:00",
        )
        lib.enabled = True
        ids = {"account": acc.id, "library": lib.id}
    manager = RunManager(config, store, Secrets.from_config(config), bus)
    return {"store": store, "bus": bus, "seen": seen, "manager": manager, **ids}


def _finish(world: dict[str, Any], mode: str, result: WorkerResult, library: bool = True) -> Any:
    run_id = world["manager"].start_run(
        account_id=world["account"], library_id=world["library"] if library else None,
        mode=mode, trigger="manual",
    )
    return world["manager"].finish_run(run_id, result)


def test_start_run_creates_running_row_with_log_path(world: dict[str, Any], config: Config) -> None:
    run_id = world["manager"].start_run(
        account_id=world["account"], library_id=world["library"], mode="sync", trigger="schedule"
    )
    with world["store"].session() as s:
        run = repo.get_run(s, run_id)
        assert run is not None and run.status == "running"
        assert run.log_path == str(config.logs_dir / f"{run_id}.log")
    assert world["seen"][-1][0] == "run.started"


def test_exit_0_updates_library_and_account(world: dict[str, Any]) -> None:
    done = {"ev": "done", "stats": {"downloaded": 3, "trashed": 1, "full": False}}
    run = _finish(world, "sync", WorkerResult(0, [{"ev": "started"}, done], done))
    assert run.status == "ok" and run.stats == done["stats"] and run.exit_code == 0
    with world["store"].session() as s:
        lib = repo.get_library(s, world["library"])
        acc = repo.get_account(s, world["account"])
        assert lib is not None and acc is not None
        assert lib.last_success_at is not None and lib.last_run_at is not None
        assert acc.status == "ok" and acc.failures == 0 and acc.backoff_until is None
    finished = {"run_id": run.id, "status": "ok", "library_id": world["library"]}
    assert ("run.finished", finished) in world["seen"]


def test_exit_3_flags_account_for_reauth(world: dict[str, Any]) -> None:
    run = _finish(world, "sync", WorkerResult(3, [{"ev": "started"}, {"ev": "need_mfa"}], None))
    assert run.status == "needs_mfa"
    with world["store"].session() as s:
        acc = repo.get_account(s, world["account"])
        assert acc is not None and acc.status == "needs_reauth"
    status = {"account_id": world["account"], "status": "needs_reauth"}
    assert ("account.status", status) in world["seen"]


def test_exit_4_marks_password_rejected(world: dict[str, Any]) -> None:
    err = {"ev": "error", "kind": "password", "message": "Invalid email/password combination."}
    run = _finish(world, "sync", WorkerResult(4, [{"ev": "started"}, err], err))
    assert run.status == "failed" and run.error_kind == "password"
    with world["store"].session() as s:
        acc = repo.get_account(s, world["account"])
        assert acc is not None and acc.status == "password_rejected"


def test_exit_5_and_1_back_off_with_doubling(world: dict[str, Any]) -> None:
    err = {"ev": "error", "kind": "unavailable", "message": "503"}
    run = _finish(world, "sync", WorkerResult(5, [err], err))
    assert run.status == "failed" and run.error_kind == "unavailable"
    with world["store"].session() as s:
        acc = repo.get_account(s, world["account"])
        assert acc is not None and acc.failures == 1 and acc.backoff_until is not None
        first = acc.backoff_until
        assert 50 <= (first - datetime.now(UTC)).total_seconds() <= 70
    _finish(world, "sync", WorkerResult(1, [], None))
    with world["store"].session() as s:
        acc = repo.get_account(s, world["account"])
        assert acc is not None and acc.failures == 2
        assert 110 <= (acc.backoff_until - datetime.now(UTC)).total_seconds() <= 130  # type: ignore[operator]
        run = repo.list_runs(s)[0]
        assert run.error_kind == "no terminal event"


def test_killed_run(world: dict[str, Any]) -> None:
    run = _finish(world, "sync", WorkerResult(-15, [{"ev": "started"}], None, killed=True))
    assert run.status == "failed" and run.error_kind == "killed"


def test_exit_6_creates_pending_plan_unless_empty_listing(
    world: dict[str, Any], config: Config
) -> None:
    plan_ev = {
        "ev": "reconcile_plan", "remote_total": 100, "local_total": 120, "extraneous": 30,
        "missing": 0, "plan_file": str(config.plans_dir / "r.txt"), "fused": True,
    }
    run = _finish(world, "reconcile", WorkerResult(6, [plan_ev], plan_ev))
    assert run.status == "fused" and run.plan_file == plan_ev["plan_file"]
    with world["store"].session() as s:
        plans = repo.list_plans(s)
        assert len(plans) == 1 and plans[0].extraneous_count == 30 and plans[0].run_id == run.id
    assert any(t == "plan.pending" for t, _ in world["seen"])
    empty = dict(plan_ev, remote_total=0)
    run2 = _finish(world, "reconcile", WorkerResult(6, [empty], empty))
    assert run2.status == "failed" and run2.error_kind == "empty_listing"
    with world["store"].session() as s:
        assert len(repo.list_plans(s)) == 1  # no plan for the empty listing
        acc = repo.get_account(s, world["account"])
        assert acc is not None and acc.failures == 1


async def test_execute_runs_the_fake_worker_and_records_progress(
    world: dict[str, Any], config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(
        "FAKE_WORKER_SCENARIO",
        json.dumps({"sync": {"events": [
            {"ev": "started"},
            {"ev": "progress", "downloaded": 2, "scanned": 5, "total": 9, "last": "x"},
            {"ev": "done", "stats": {"downloaded": 2, "trashed": 0, "full": False}},
        ], "exit": 0}}),
    )
    with world["store"].session() as s:
        acc = repo.get_account(s, world["account"])
        lib = repo.get_library(s, world["library"])
        assert acc is not None and lib is not None
        job = build_job(config, acc, lib, interactive=False)
    run_id = world["manager"].start_run(
        account_id=world["account"], library_id=world["library"], mode="sync", trigger="manual"
    )
    result = await world["manager"].execute(
        run_id, mode="sync", job=job, password="pw", interactive=False
    )
    assert result.exit_code == 0
    with world["store"].session() as s:
        run = repo.get_run(s, run_id)
        assert run is not None and run.status == "ok"
        assert run.stats["downloaded"] == 2 and run.stats["progress"]["scanned"] == 5
    topics = [t for t, _ in world["seen"]]
    assert topics.count("run.event") == 3 and topics[-1] == "run.finished"


async def test_progress_db_failure_does_not_drop_events(
    world: dict[str, Any], config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(
        "FAKE_WORKER_SCENARIO",
        json.dumps({"sync": {"events": [
            {"ev": "started"},
            {"ev": "progress", "downloaded": 1, "scanned": 1, "total": 2, "last": "x"},
            {"ev": "done", "stats": {"downloaded": 1, "trashed": 0, "full": False}},
        ], "exit": 0}}),
    )

    def boom(run_id: str, event: dict[str, Any]) -> None:
        raise RuntimeError("db locked")

    monkeypatch.setattr(world["manager"], "_record_progress", boom)
    with world["store"].session() as s:
        acc = repo.get_account(s, world["account"])
        lib = repo.get_library(s, world["library"])
        assert acc is not None and lib is not None
        job = build_job(config, acc, lib, interactive=False)
    run_id = world["manager"].start_run(
        account_id=world["account"], library_id=world["library"], mode="sync", trigger="manual"
    )
    result = await world["manager"].execute(
        run_id, mode="sync", job=job, password="pw", interactive=False
    )
    assert result.exit_code == 0
    assert [t for t, _ in world["seen"]].count("run.event") == 3  # progress still published


async def test_finish_run_crash_marks_run_internal_failure(
    world: dict[str, Any], config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken_finish(run_id: str, result: Any) -> Any:
        raise RuntimeError("bookkeeping bug")

    monkeypatch.setattr(world["manager"], "finish_run", broken_finish)
    with world["store"].session() as s:
        acc = repo.get_account(s, world["account"])
        lib = repo.get_library(s, world["library"])
        assert acc is not None and lib is not None
        job = build_job(config, acc, lib, interactive=False)
    run_id = world["manager"].start_run(
        account_id=world["account"], library_id=world["library"], mode="sync", trigger="manual"
    )
    with pytest.raises(RuntimeError, match="bookkeeping"):
        await world["manager"].execute(
            run_id, mode="sync", job=job, password="pw", interactive=False
        )
    with world["store"].session() as s:
        run = repo.get_run(s, run_id)
        assert run is not None and run.status == "failed" and run.error_kind == "internal"
        assert run.ended_at is not None
    finished = {"run_id": run_id, "status": "failed", "library_id": None}
    assert ("run.finished", finished) in world["seen"]
