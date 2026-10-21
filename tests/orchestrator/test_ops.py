from __future__ import annotations

import errno
import os
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from icloud2nas.orchestrator.bus import EventBus
from icloud2nas.orchestrator.config import Config
from icloud2nas.orchestrator.onboarding import Onboarding
from icloud2nas.orchestrator.ops import Busy, Ops
from icloud2nas.orchestrator.runs import RunManager
from icloud2nas.orchestrator.scheduler import Scheduler
from icloud2nas.orchestrator.settings import Settings
from icloud2nas.orchestrator.store import repo
from icloud2nas.orchestrator.store.db import Store, make_engine, upgrade_db
from icloud2nas.orchestrator.store.models import ReconcilePlan, Run
from icloud2nas.orchestrator.store.secrets import Secrets


@pytest.fixture
def ops(config: Config) -> dict[str, Any]:
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
    sched = Scheduler(
        config, store, secrets, bus, runs, onboarding, settings, jitter_s=(0, 0), tick_s=0.01
    )
    ids: dict[str, str] = {}
    with store.session() as s:
        for name, apple_id in (("旭刚", "a@icloud.com"), ("幻伊镜", "b@icloud.com")):
            acc = repo.create_account(
                s, display_name=name, apple_id=apple_id, region="cn",
                password_enc=secrets.encrypt("pw"),
                client_id="11111111-1111-1111-1111-111111111111",
            )
            acc.status = "ok"
            lib = repo.upsert_library(
                s, acc, kind="personal", zone_name="PrimarySync", display_name=name,
                target_dir=name, interval_min=30, nightly_at="03:00",
            )
            lib.enabled = True
            ids[name] = lib.id
            ids[name + "_acc"] = acc.id
            (config.photos_dir / name).mkdir(parents=True, exist_ok=True)
            config.cookie_dir(acc.id).mkdir(parents=True, exist_ok=True)
    return {
        "ops": Ops(config, store, settings, bus, sched, onboarding),
        "store": store, "settings": settings, "sched": sched, "seen": seen,
        "config": config, "onboarding": onboarding, **ids,
    }


def _lib(w: dict[str, Any], name: str = "旭刚") -> Any:
    with w["store"].session() as s:
        lib = repo.get_library(s, w[name])
        assert lib is not None
        s.expunge(lib)
        return lib


def _topics(w: dict[str, Any]) -> list[str]:
    return [t for t, _ in w["seen"]]


def test_rename_moves_folder_and_trash(ops: dict[str, Any]) -> None:
    photos: Path = ops["config"].photos_dir
    (photos / "旭刚" / "2026").mkdir()
    (photos / "旭刚" / "2026" / "a.jpg").write_bytes(b"a")
    (photos / ".trash" / "旭刚" / "2026-10-01").mkdir(parents=True)
    (photos / ".trash" / "旭刚" / "2026-10-01" / "b.jpg").write_bytes(b"b")
    ops["ops"].set_library(ops["旭刚"], target_dir="家/旭刚2")
    assert (photos / "家" / "旭刚2" / "2026" / "a.jpg").read_bytes() == b"a"
    assert (photos / ".trash" / "家" / "旭刚2" / "2026-10-01" / "b.jpg").read_bytes() == b"b"
    assert not (photos / "旭刚").exists() and not (photos / ".trash" / "旭刚").exists()
    assert _lib(ops).target_dir == "家/旭刚2"
    assert "library.updated" in _topics(ops)


def test_set_library_fields(ops: dict[str, Any]) -> None:
    ops["ops"].set_library(
        ops["旭刚"], display_name="X", interval_min=60, nightly_at="4:30", enabled=False
    )
    lib = _lib(ops)
    assert (lib.display_name, lib.interval_min, lib.nightly_at, lib.enabled, lib.status) == (
        "X", 60, "04:30", False, "ok"
    )
    for bad in (4, 1441):
        with pytest.raises(ValueError):
            ops["ops"].set_library(ops["旭刚"], interval_min=bad)
    with pytest.raises(ValueError):
        ops["ops"].set_library(ops["旭刚"], nightly_at="25:00")


def test_rename_refused_while_running(ops: dict[str, Any]) -> None:
    ops["sched"].running_libraries.add(ops["旭刚"])
    with pytest.raises(Busy):
        ops["ops"].set_library(ops["旭刚"], target_dir="新")
    assert _lib(ops).target_dir == "旭刚"


def test_rename_refused_when_nesting(ops: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        ops["ops"].set_library(ops["旭刚"], target_dir="幻伊镜/sub")
    assert (ops["config"].photos_dir / "旭刚").is_dir()


def test_rename_refused_when_destination_non_empty(ops: dict[str, Any]) -> None:
    dest = ops["config"].photos_dir / "新"
    dest.mkdir()
    (dest / "f").write_bytes(b"1")
    with pytest.raises(ValueError):
        ops["ops"].set_library(ops["旭刚"], target_dir="新")
    assert _lib(ops).target_dir == "旭刚"


def test_rename_cross_device(ops: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(src: Any, dst: Any) -> None:
        raise OSError(errno.EXDEV, "cross-device")

    monkeypatch.setattr(os, "rename", boom)
    with pytest.raises(ValueError, match="cross-device"):
        ops["ops"].set_library(ops["旭刚"], target_dir="新")
    assert _lib(ops).target_dir == "旭刚"
    assert (ops["config"].photos_dir / "旭刚").is_dir()


def test_pause_resume_library_and_global(ops: dict[str, Any]) -> None:
    o = ops["ops"]
    o.pause_library(ops["旭刚"])
    assert _lib(ops).status == "paused"
    with pytest.raises(ValueError, match="paused"):
        o.pause_library(ops["旭刚"])
    o.resume_library(ops["旭刚"])
    assert _lib(ops).status == "ok"
    o.resume_library(ops["旭刚"])  # ok: no-op
    with ops["store"].session() as s:
        repo.get_library(s, ops["旭刚"]).status = "space_paused"  # type: ignore[union-attr]
    with pytest.raises(ValueError, match="space_paused"):
        o.resume_library(ops["旭刚"])
    o.pause_all()
    assert ops["settings"].get("paused") is True and ops["sched"].paused()
    o.resume_all()
    assert ops["settings"].get("paused") is False
    assert ("paused", {"paused": True}) in ops["seen"]
    assert ("paused", {"paused": False}) in ops["seen"]


def test_request_run_enqueues_full(ops: dict[str, Any]) -> None:
    ops["ops"].request_run(ops["旭刚"], "sync")
    ops["ops"].request_run(ops["幻伊镜"], "reconcile")
    got = {(j.library_id, j.kind, j.trigger) for j in ops["sched"].pending_manual()}
    assert got == {(ops["旭刚"], "full", "manual"), (ops["幻伊镜"], "reconcile", "manual")}
    with pytest.raises(ValueError):
        ops["ops"].request_run(ops["旭刚"], "bogus")  # type: ignore[arg-type]


def test_approve_then_reject_rules(ops: dict[str, Any]) -> None:
    with ops["store"].session() as s:
        run = Run(account_id=ops["旭刚_acc"], library_id=ops["旭刚"], mode="reconcile",
                  trigger="manual")
        s.add(run)
        s.flush()
        plans = [repo.create_plan(s, library_id=ops["旭刚"], run_id=run.id,
                                  extraneous_count=1, plan_file="p") for _ in range(3)]
        ids = [p.id for p in plans]
    o = ops["ops"]
    o.approve_plan(ids[0])
    assert [(j.kind, j.plan_id) for j in ops["sched"].pending_manual()] == [("apply_plan", ids[0])]
    with pytest.raises(ValueError):
        o.approve_plan(ids[0])
    with pytest.raises(ValueError):
        o.reject_plan(ids[0])
    o.reject_plan(ids[1])
    assert ("plan.resolved", {"plan_id": ids[1], "library_id": ops["旭刚"],
                              "status": "rejected"}) in ops["seen"]
    with pytest.raises(ValueError):
        o.reject_plan(ids[1])
    # enqueue failure rolls the plan back to pending
    ops["sched"].running_libraries  # noqa: B018
    with ops["store"].session() as s:
        repo.get_library(s, ops["旭刚"]).enabled = False  # type: ignore[union-attr]
    with pytest.raises(ValueError):
        o.approve_plan(ids[2])
    with ops["store"].session() as s:
        assert s.get(ReconcilePlan, ids[2]).status == "pending"  # type: ignore[union-attr]


def test_delete_account_moves_dirs_to_trash(ops: dict[str, Any]) -> None:
    photos: Path = ops["config"].photos_dir
    (photos / "旭刚" / "x.jpg").write_bytes(b"x")
    with ops["store"].session() as s:
        s.add(Run(account_id=ops["旭刚_acc"], library_id=ops["旭刚"], mode="sync",
                  trigger="manual"))
    ops["ops"].delete_account(ops["旭刚_acc"], keep_dir=False)
    assert (photos / ".trash" / "旭刚" / date.today().isoformat() / "旭刚" / "x.jpg").exists()
    assert not (photos / "旭刚").exists()
    assert not ops["config"].cookie_dir(ops["旭刚_acc"]).parent.exists()
    with ops["store"].session() as s:
        assert repo.get_account(s, ops["旭刚_acc"]) is None
        assert repo.get_library(s, ops["旭刚"]) is None
        assert [lib.display_name for lib in repo.list_libraries(s)] == ["幻伊镜"]
    assert ("account.updated", {"account_id": ops["旭刚_acc"], "deleted": True}) in ops["seen"]
    with pytest.raises(KeyError):
        ops["ops"].delete_account(ops["旭刚_acc"], keep_dir=False)


def test_delete_account_keep_dir(ops: dict[str, Any]) -> None:
    ops["ops"].delete_account(ops["幻伊镜_acc"], keep_dir=True)
    assert (ops["config"].photos_dir / "幻伊镜").is_dir()


def test_delete_account_busy(ops: dict[str, Any]) -> None:
    ops["sched"].running_accounts.add(ops["旭刚_acc"])
    with pytest.raises(Busy):
        ops["ops"].delete_account(ops["旭刚_acc"], keep_dir=False)
    ops["sched"].running_accounts.clear()
    ops["onboarding"].sessions[ops["旭刚_acc"]] = object()
    with pytest.raises(Busy):
        ops["ops"].delete_account(ops["旭刚_acc"], keep_dir=False)
    with ops["store"].session() as s:
        assert repo.get_account(s, ops["旭刚_acc"]) is not None


async def test_start_auth_busy_and_set_password(ops: dict[str, Any]) -> None:
    ops["sched"].running_accounts.add(ops["旭刚_acc"])
    with pytest.raises(Busy):
        await ops["ops"].start_auth(ops["旭刚_acc"])
    ops["sched"].running_accounts.clear()
    run_id = await ops["ops"].set_password(ops["旭刚_acc"], "new")
    assert run_id
    await ops["onboarding"].wait_auth(ops["旭刚_acc"])
    with pytest.raises(KeyError):
        await ops["ops"].submit_code(ops["旭刚_acc"], "1")
