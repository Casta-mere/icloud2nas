from __future__ import annotations

import asyncio
import errno
import json
import os
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from icloud2nas.engine.trash import DAY_MARKER, kept_dir, move_to_trash, trash_day_dir
from icloud2nas.orchestrator import owners
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


def test_rename_library_moves_trash_and_kept_together(ops: dict[str, Any]) -> None:
    photos: Path = ops["config"].photos_dir
    lib = photos / "旭刚"
    (lib / "2026").mkdir()
    (lib / "2026" / "a.jpg").write_bytes(b"a")
    day = trash_day_dir(lib, date(2026, 10, 1))
    day.mkdir(parents=True)
    (day / DAY_MARKER).touch()
    (day / "b.jpg").write_bytes(b"b")
    (kept_dir(lib) / "k.jpg").parent.mkdir(parents=True)
    (kept_dir(lib) / "k.jpg").write_bytes(b"k")
    ops["ops"].set_library(ops["旭刚"], target_dir="家/旭刚2")
    new = photos / "家" / "旭刚2"
    assert (new / "2026" / "a.jpg").read_bytes() == b"a"
    assert (trash_day_dir(new, date(2026, 10, 1)) / "b.jpg").read_bytes() == b"b"
    assert (kept_dir(new) / "k.jpg").read_bytes() == b"k"
    assert not lib.exists() and not (photos / ".trash").exists()
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


def _at(name: Any, dir_fd: int | None) -> Path:
    """The path a dir_fd-relative name stands for (via /proc), or name itself."""
    if dir_fd is None:
        return Path(name)
    return (Path(os.readlink(f"/proc/self/fd/{dir_fd}")) / name).resolve()


def test_rename_cross_device(ops: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_: Any, **__: Any) -> None:
        raise OSError(errno.EXDEV, "cross-device")

    monkeypatch.setattr(os, "rename", boom)
    with pytest.raises(ValueError, match="cross-device"):
        ops["ops"].set_library(ops["旭刚"], target_dir="新")
    assert _lib(ops).target_dir == "旭刚"
    assert (ops["config"].photos_dir / "旭刚").is_dir()


def test_rename_uses_a_single_rename(
    ops: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    photos: Path = ops["config"].photos_dir
    (photos / "旭刚" / "a.jpg").write_bytes(b"a")
    move_to_trash(photos / "旭刚", photos / "旭刚" / "a.jpg", date(2026, 10, 1))
    real = os.rename
    calls: list[tuple[Any, Any]] = []

    def counting(src: Any, dst: Any, **fds: Any) -> None:
        calls.append((_at(src, fds.get("src_dir_fd")), _at(dst, fds.get("dst_dir_fd"))))
        real(src, dst, **fds)

    monkeypatch.setattr(os, "rename", counting)
    ops["ops"].set_library(ops["旭刚"], target_dir="新")
    assert calls == [(photos / "旭刚", photos / "新")]
    assert list((photos / "新" / ".icloud2nas" / "trash").rglob("a.jpg"))


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


def test_delete_account_trash_option_renames_to_deleted_and_records_row(
    ops: dict[str, Any],
) -> None:
    photos: Path = ops["config"].photos_dir
    (photos / "旭刚" / "x.jpg").write_bytes(b"x")
    with ops["store"].session() as s:
        s.add(Run(account_id=ops["旭刚_acc"], library_id=ops["旭刚"], mode="sync",
                  trigger="manual"))
    ops["ops"].delete_account(ops["旭刚_acc"], keep_dir=False)
    dest = photos / ".icloud2nas-deleted" / f"旭刚-{date.today().isoformat()}"
    assert (dest / "x.jpg").read_bytes() == b"x"
    assert not (photos / "旭刚").exists() and not (photos / ".trash").exists()
    with ops["store"].session() as s:
        rows = [(r.path, r.deleted_at is not None) for r in repo.list_deleted_dirs(s)]
    assert rows == [(dest.relative_to(photos).as_posix(), True)]
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
    ops["sched"].hold_account(ops["旭刚_acc"], "run-1")
    with pytest.raises(Busy):
        ops["ops"].delete_account(ops["旭刚_acc"], keep_dir=False)
    ops["sched"].running_accounts.clear()
    ops["onboarding"].sessions[ops["旭刚_acc"]] = object()
    with pytest.raises(Busy):
        ops["ops"].delete_account(ops["旭刚_acc"], keep_dir=False)
    with ops["store"].session() as s:
        assert repo.get_account(s, ops["旭刚_acc"]) is not None


async def test_start_auth_busy_and_set_password(ops: dict[str, Any]) -> None:
    ops["sched"].hold_account(ops["旭刚_acc"], "run-1")
    with pytest.raises(Busy):
        await ops["ops"].start_auth(ops["旭刚_acc"])
    ops["sched"].running_accounts.clear()
    run_id = await ops["ops"].set_password(ops["旭刚_acc"], "new")
    assert run_id
    await ops["onboarding"].wait_auth(ops["旭刚_acc"])
    with pytest.raises(KeyError):
        await ops["ops"].submit_code(ops["旭刚_acc"], "1")
    await ops["ops"].shutdown()


def _auth_waits_for_code(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "FAKE_WORKER_SCENARIO",
        json.dumps({"auth": {"events": [{"ev": "started"}, {"wait_code": "123456"}], "exit": 0}}),
    )


async def test_concurrent_start_auth_yields_one_session(
    ops: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    _auth_waits_for_code(monkeypatch)
    acc = ops["旭刚_acc"]
    results = await asyncio.gather(
        ops["ops"].start_auth(acc), ops["ops"].start_auth(acc), return_exceptions=True
    )
    assert sum(isinstance(r, str) for r in results) == 1
    assert sum(isinstance(r, Busy) for r in results) == 1
    session = ops["onboarding"].sessions[acc]
    assert session.run_id in results
    with ops["store"].session() as s:
        assert len([r for r in repo.list_runs(s) if r.mode == "auth"]) == 1
    await ops["sched"].shutdown()
    await ops["ops"].shutdown()


@pytest.mark.parametrize("auth_first", [True, False])
async def test_start_auth_racing_tick_never_overlaps_on_one_account(
    ops: dict[str, Any], monkeypatch: pytest.MonkeyPatch, auth_first: bool
) -> None:
    _auth_waits_for_code(monkeypatch)
    sched, acc = ops["sched"], ops["旭刚_acc"]
    with ops["store"].session() as s:  # only 旭刚's library is due
        repo.get_library(s, ops["幻伊镜"]).enabled = False  # type: ignore[union-attr]
    calls = [ops["ops"].start_auth(acc), sched.tick()]
    results = await asyncio.gather(*(calls if auth_first else calls[::-1]), return_exceptions=True)
    if not auth_first:
        results = results[::-1]
    auth, launched = results
    if auth_first:
        assert isinstance(auth, str) and launched == 0
    else:
        assert isinstance(auth, Busy) and launched == 1
    with ops["store"].session() as s:
        modes = [r.mode for r in repo.list_runs(s) if r.account_id == acc]
    assert modes == (["auth"] if auth_first else ["sync"])
    await sched.shutdown()
    await ops["ops"].shutdown()


async def test_discovery_release_never_clears_a_sync_hold(ops: dict[str, Any]) -> None:
    sched, acc = ops["sched"], ops["旭刚_acc"]
    assert sched.hold_account(acc, "run-sync")
    # a discovery chain whose auth session is gone ends at once and releases its own token
    await ops["ops"]._discover_after_auth(acc, f"discovery:{acc}")
    assert sched.running_accounts == {acc: "run-sync"}
    with pytest.raises(Busy):
        await ops["ops"].refresh_libraries(acc)
    assert sched.running_accounts == {acc: "run-sync"}


async def test_start_auth_spawn_failure_releases_the_hold(
    ops: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    async def boom(*args: Any, **kwargs: Any) -> Any:
        raise OSError("no worker binary")

    monkeypatch.setattr(ops["onboarding"], "start_auth", boom)
    with pytest.raises(OSError):
        await ops["ops"].start_auth(ops["旭刚_acc"])
    assert ops["sched"].running_accounts == {} and not ops["ops"].discovering


def test_delete_account_drops_its_queued_jobs(ops: dict[str, Any]) -> None:
    ops["ops"].request_run(ops["旭刚"], "sync")
    ops["ops"].request_run(ops["幻伊镜"], "sync")
    ops["ops"].delete_account(ops["旭刚_acc"], keep_dir=True)
    assert [j.library_id for j in ops["sched"].pending_manual()] == [ops["幻伊镜"]]


def _second_library(ops: dict[str, Any]) -> Path:
    photos: Path = ops["config"].photos_dir
    with ops["store"].session() as s:
        acc = repo.get_account(s, ops["旭刚_acc"])
        assert acc is not None
        repo.upsert_library(
            s, acc, kind="shared", zone_name="SharedSync-1", display_name="家",
            target_dir="shared/家", interval_min=30, nightly_at="03:00",
        )
    (photos / "shared" / "家").mkdir(parents=True)
    (photos / "shared" / "家" / "s.jpg").write_bytes(b"s")
    (photos / "旭刚" / "x.jpg").write_bytes(b"x")
    return photos


def _rows_intact(ops: dict[str, Any]) -> None:
    with ops["store"].session() as s:
        assert repo.get_account(s, ops["旭刚_acc"]) is not None
        assert len(repo.libraries_of_account(s, ops["旭刚_acc"])) == 2


def test_delete_account_refuses_cross_device_before_moving(
    ops: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    photos = _second_library(ops)
    real_stat = os.stat

    def fake_stat(path: Any, *args: Any, **kwargs: Any) -> Any:
        st = real_stat(path, *args, **kwargs)
        if Path(path) == photos / "shared" / "家":  # the second library sits on another disk
            return os.stat_result((*st[:2], st.st_dev + 1, *st[3:]))
        return st

    monkeypatch.setattr(os, "stat", fake_stat)
    with pytest.raises(ValueError, match="cross-device"):
        ops["ops"].delete_account(ops["旭刚_acc"], keep_dir=False)
    assert (photos / "旭刚" / "x.jpg").exists() and (photos / "shared" / "家" / "s.jpg").exists()
    _rows_intact(ops)


@pytest.mark.parametrize("err", [errno.EXDEV, errno.EBUSY])
def test_delete_account_rename_failure_moves_nothing(
    ops: dict[str, Any], monkeypatch: pytest.MonkeyPatch, err: int
) -> None:
    photos = _second_library(ops)
    real = os.rename
    calls: list[int] = []

    def flaky(src: Any, dst: Any, **fds: Any) -> None:
        calls.append(1)
        if len(calls) == 2:  # the first library moved; the second fails
            raise OSError(err, os.strerror(err))
        real(src, dst, **fds)

    monkeypatch.setattr(os, "rename", flaky)
    with pytest.raises(ValueError, match="could not move"):
        ops["ops"].delete_account(ops["旭刚_acc"], keep_dir=False)
    assert (photos / "旭刚" / "x.jpg").read_bytes() == b"x"  # moved back
    assert (photos / "shared" / "家" / "s.jpg").exists()
    assert not (photos / ".icloud2nas-deleted").exists()
    assert not (photos / "shared" / ".icloud2nas-deleted").exists()
    with ops["store"].session() as s:
        assert repo.list_deleted_dirs(s) == []
    assert ops["config"].cookie_dir(ops["旭刚_acc"]).is_dir()
    _rows_intact(ops)


@pytest.mark.parametrize("target", ["旭刚/iCloud", "旭刚/a/b"])
def test_rename_into_own_subtree_is_refused(ops: dict[str, Any], target: str) -> None:
    with pytest.raises(ValueError, match="contain one another"):
        ops["ops"].set_library(ops["旭刚"], target_dir=target)
    assert _lib(ops).target_dir == "旭刚" and (ops["config"].photos_dir / "旭刚").is_dir()


def test_rename_to_own_parent_is_refused(ops: dict[str, Any]) -> None:
    ops["ops"].set_library(ops["旭刚"], target_dir="家/旭刚")
    with pytest.raises(ValueError, match="contain one another"):
        ops["ops"].set_library(ops["旭刚"], target_dir="家")
    assert _lib(ops).target_dir == "家/旭刚"


@pytest.fixture
def chowned(monkeypatch: pytest.MonkeyPatch) -> list[tuple[Path, int, int]]:
    """Privileged mode with chown recorded instead of performed."""
    calls: list[tuple[Path, int, int]] = []
    monkeypatch.setattr(owners, "privileged", lambda: True)
    monkeypatch.setattr(
        owners.os, "chown", lambda p, u, g, **_: calls.append((Path(p), u, g))
    )
    monkeypatch.setattr(
        owners.os, "fchown", lambda fd, u, g: calls.append((_at(".", fd), u, g))
    )
    return calls


def _set_owner(w: dict[str, Any], uid: int, gid: int) -> None:
    with w["store"].session() as s:
        acc = repo.get_account(s, w["旭刚_acc"])
        assert acc is not None
        acc.owner_uid, acc.owner_gid = uid, gid


def test_rename_hands_created_parents_to_the_owner(
    ops: dict[str, Any], chowned: list[tuple[Path, int, int]]
) -> None:
    photos: Path = ops["config"].photos_dir
    _set_owner(ops, 1000, 10)
    ops["ops"].set_library(ops["旭刚"], target_dir="Castamere/Photos/iCloud")
    assert (photos / "Castamere" / "Photos" / "iCloud").is_dir()
    assert chowned == [
        (photos / "Castamere", 1000, 10),
        (photos / "Castamere" / "Photos", 1000, 10),
    ]  # the renamed folder itself keeps its owner


def test_delete_hands_the_new_deleted_root_to_the_owner(
    ops: dict[str, Any], chowned: list[tuple[Path, int, int]]
) -> None:
    photos: Path = ops["config"].photos_dir
    _set_owner(ops, 1000, 10)
    ops["ops"].delete_account(ops["旭刚_acc"], keep_dir=False)
    assert chowned == [(photos / ".icloud2nas-deleted", 1000, 10)]


def test_change_folder_refuses_a_symlinked_parent(ops: dict[str, Any]) -> None:
    photos: Path = ops["config"].photos_dir
    (photos / "Ann" / "iCloud").mkdir(parents=True)
    (photos / "Eve").symlink_to(photos / "Ann")  # Eve swapped her home for a link to Ann's
    with pytest.raises(ValueError, match="symlink"):
        ops["ops"].set_library(ops["旭刚"], target_dir="Eve/Photos")
    assert _lib(ops).target_dir == "旭刚" and (photos / "旭刚").is_dir()
    assert sorted(p.name for p in (photos / "Ann").iterdir()) == ["iCloud"]


def test_delete_account_refuses_a_symlinked_parent(ops: dict[str, Any]) -> None:
    photos: Path = ops["config"].photos_dir
    real = photos / "Ann"
    (real / "家").mkdir(parents=True)
    ops["ops"].set_library(ops["旭刚"], target_dir="Eve/家")  # a real folder at first
    (photos / "Eve" / "家").rename(real / "家")
    (photos / "Eve").rmdir()
    (photos / "Eve").symlink_to(real)  # then the parent is swapped for a link
    with pytest.raises(ValueError, match="symlink"):
        ops["ops"].delete_account(ops["旭刚_acc"], keep_dir=False)
    assert (real / "家").is_dir() and not (real / ".icloud2nas-deleted").exists()
