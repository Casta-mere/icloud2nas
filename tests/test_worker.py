from __future__ import annotations

import io
import json
import logging
import threading
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from icloudpd.status import Status, StatusExchange
from pyicloud_ipd.exceptions import (
    PyiCloudFailedLoginException,
    PyiCloudServiceUnavailableException,
)

from icloud2nas import worker as worker_mod
from icloud2nas.engine.errors import ExitCode, HardExit
from icloud2nas.engine.job import JOB_ENV, PASSWORD_ENV, FilePolicy
from icloud2nas.engine.paths import expected_paths
from tests.fakes import FakeICloud, FakeLibrary, make_asset

BASE = {
    "account_id": "acc1",
    "apple_id": "a@icloud.com",
    "region": "cn",
    "client_id": "11111111-1111-1111-1111-111111111111",
    "cookie_dir": "/tmp/i2n-test-cookies",
}


def raising_exit(code: ExitCode) -> None:
    raise HardExit(code)


def run_main(
    mode: str,
    job: dict[str, Any],
    *,
    stdin: str = "",
    password: str | None = "pw",
    apply_plan: str | None = None,
) -> tuple[int, list[dict[str, Any]]]:
    env = {JOB_ENV: json.dumps(job)}
    if password is not None:
        env[PASSWORD_ENV] = password
    out = io.StringIO()
    argv = ["--mode", mode, "--run-id", "run-1"]
    if apply_plan:
        argv += ["--apply-plan", apply_plan]
    rc = worker_mod.main(
        argv, env=env, stdin=io.StringIO(stdin), stdout=out, exit_hook=raising_exit
    )
    return rc, [json.loads(line) for line in out.getvalue().splitlines()]


def evs(events: list[dict[str, Any]]) -> list[str]:
    return [e["ev"] for e in events]


def fake_login_with_mfa(expected_code: str) -> Callable[..., FakeICloud]:
    """An adapter.authenticate stand-in that behaves like icloudpd's web MFA flow."""

    def authenticate(
        spec: Any,
        password: str,
        exchange: StatusExchange,
        notificator: Callable[[], None],
        logger: Any,
    ) -> FakeICloud:
        notificator()  # icloudpd calls this before asking for the code
        assert exchange.replace_status(Status.NO_INPUT_NEEDED, Status.NEED_MFA)
        while exchange.get_status() is Status.NEED_MFA:
            threading.Event().wait(0.01)
        assert exchange.replace_status(Status.SUPPLIED_MFA, Status.CHECKING_MFA)
        if exchange.get_payload() != expected_code:
            exchange.set_error("Failed to verify two-factor authentication code")
            while exchange.get_status() is Status.NEED_MFA:
                threading.Event().wait(0.01)
            assert exchange.replace_status(Status.SUPPLIED_MFA, Status.CHECKING_MFA)
        assert exchange.replace_status(Status.CHECKING_MFA, Status.NO_INPUT_NEEDED)
        return FakeICloud()

    return authenticate


def test_main_without_job_env_is_config_error() -> None:
    out = io.StringIO()
    rc = worker_mod.main(["--mode", "auth", "--run-id", "r"], env={}, stdout=out)
    assert rc == 1
    event = json.loads(out.getvalue())
    assert event["ev"] == "error" and event["kind"] == "config"


def test_main_without_password_is_config_error() -> None:
    rc, events = run_main("auth", {**BASE, "interactive": True}, password=None)
    assert rc == 1 and events[-1]["kind"] == "config" and PASSWORD_ENV in events[-1]["message"]


def test_parse_args_rejects_unknown_mode() -> None:
    with pytest.raises(SystemExit):
        worker_mod.parse_args(["--mode", "fly", "--run-id", "r"])


def test_auth_interactive_round_trips_code(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(worker_mod.adapter, "authenticate", fake_login_with_mfa("123456"))
    rc, events = run_main(
        "auth", {**BASE, "interactive": True}, stdin='{"code": "000000"}\n{"code": "123456"}\n'
    )
    assert rc == 0
    assert evs(events) == ["started", "need_mfa", "mfa_result", "mfa_result", "done"]
    assert events[2]["ok"] is False and events[3]["ok"] is True
    assert events[0]["mode"] == "auth" and events[0]["run_id"] == "run-1"


def test_auth_times_out_waiting_for_code(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(worker_mod.adapter, "authenticate", fake_login_with_mfa("123456"))
    rc, events = run_main("auth", {**BASE, "interactive": True, "mfa_timeout_s": 0}, stdin="")
    assert rc == 1
    assert evs(events)[-1] == "error" and events[-1]["kind"] == "mfa_timeout"


def test_non_interactive_mfa_exits_3(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(worker_mod.adapter, "authenticate", fake_login_with_mfa("123456"))
    rc, events = run_main("list", {**BASE, "interactive": False})
    assert rc == 3
    assert evs(events) == ["started", "need_mfa"]


def test_password_rejected_exits_4(monkeypatch: pytest.MonkeyPatch) -> None:
    def bad(*args: Any) -> None:
        raise PyiCloudFailedLoginException("Invalid email/password combination.")

    monkeypatch.setattr(worker_mod.adapter, "authenticate", bad)
    rc, events = run_main("list", BASE)
    assert rc == 4
    assert events[-1]["ev"] == "error" and events[-1]["kind"] == "password"


def test_list_emits_libraries(monkeypatch: pytest.MonkeyPatch) -> None:
    icloud = FakeICloud()
    icloud.photos.private_libraries = {"PrimarySync": icloud.photos}
    icloud.photos.shared_libraries = {"SharedSync-9": FakeLibrary()}
    monkeypatch.setattr(worker_mod.adapter, "authenticate", lambda *a: icloud)
    rc, events = run_main("list", BASE)
    assert rc == 0
    libs = next(e for e in events if e["ev"] == "libraries")
    assert libs["personal"] == ["PrimarySync"] and libs["shared"] == ["SharedSync-9"]
    assert events[-1]["ev"] == "done" and events[-1]["stats"] == {"personal": 1, "shared": 1}


def test_unexpected_exception_is_reported_not_raised(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*args: Any) -> None:
        raise RuntimeError("kaboom")

    monkeypatch.setattr(worker_mod.adapter, "authenticate", boom)
    rc, events = run_main("auth", {**BASE, "interactive": True})
    assert rc == 1 and events[-1] == {**events[-1], "ev": "error", "kind": "unknown"}


def test_download_counter_counts_icloudpd_downloaded_lines() -> None:
    logger, counter, _recorder = worker_mod.setup_logging()
    logger.info("Downloaded %s", "x.HEIC")
    logger.info("Downloaded %s", "y.MOV")
    logger.info("%s already exists", "x.HEIC")
    assert counter.downloaded == 2
    assert logger.name == "icloudpd" and logger.level == logging.INFO


SH = ZoneInfo("Asia/Shanghai")
TAKEN = datetime(2024, 5, 3, 10, 0, tzinfo=SH)


def _sync_job(photos: Path, lib: Path, **extra: Any) -> dict[str, Any]:
    return {**BASE, "photos_root": str(photos), "target_dir": str(lib), **extra}


def _touch_expected(asset: Any, lib: Path) -> Path:
    (path,) = expected_paths(asset, str(lib), FilePolicy())
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_bytes(b"x")
    return Path(path)


def test_sync_downloads_then_trashes_recently_deleted(
    photos_tree: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    photos, lib = photos_tree
    deleted = make_asset("DEL1", "IMG_7.HEIC", TAKEN)
    kept = make_asset("KEEP", "IMG_8.HEIC", TAKEN)
    deleted_path = _touch_expected(deleted, lib)
    kept_path = _touch_expected(kept, lib)
    icloud = FakeICloud()
    icloud.photos.recently_deleted = [deleted]
    monkeypatch.setattr(worker_mod.adapter, "authenticate", lambda *a: icloud)

    def fake_run_sync(
        spec: Any, password: str, exchange: Any, notificator: Any, logger: Any
    ) -> int:
        logger.info("Downloaded %s", "IMG_9.HEIC")
        logger.info("Downloaded %s", "IMG_9.MOV")
        return 0

    monkeypatch.setattr(worker_mod.adapter, "run_sync", fake_run_sync)
    rc, events = run_main("sync", _sync_job(photos, lib))
    assert rc == 0
    assert events[-1]["ev"] == "done"
    assert events[-1]["stats"] == {"downloaded": 2, "trashed": 1, "full": False}
    assert not deleted_path.exists() and kept_path.exists()
    assert list((photos / ".trash" / "旭刚").rglob("IMG_7_*.HEIC"))


def test_sync_dry_run_trashes_nothing(
    photos_tree: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    photos, lib = photos_tree
    deleted = make_asset("DEL2", "IMG_7.HEIC", TAKEN)
    deleted_path = _touch_expected(deleted, lib)
    icloud = FakeICloud()
    icloud.photos.recently_deleted = [deleted]
    monkeypatch.setattr(worker_mod.adapter, "authenticate", lambda *a: icloud)
    monkeypatch.setattr(worker_mod.adapter, "run_sync", lambda *a: 0)
    rc, events = run_main("sync", _sync_job(photos, lib, dry_run=True))
    assert rc == 0 and events[-1]["stats"]["trashed"] == 1  # counted, not moved
    assert deleted_path.exists()


def test_sync_icloudpd_failure_is_error(
    photos_tree: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    photos, lib = photos_tree
    monkeypatch.setattr(worker_mod.adapter, "authenticate", lambda *a: FakeICloud())
    monkeypatch.setattr(worker_mod.adapter, "run_sync", lambda *a: 1)
    rc, events = run_main("sync", _sync_job(photos, lib))
    assert rc == 1 and events[-1]["ev"] == "error" and events[-1]["kind"] == "icloudpd"


def test_sync_uses_requested_shared_library(
    photos_tree: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    photos, lib = photos_tree
    shared_lib = photos / "shared" / "家庭"
    shared_lib.mkdir(parents=True)
    deleted = make_asset("SDEL", "IMG_S.HEIC", TAKEN)
    deleted_path = _touch_expected(deleted, shared_lib)
    icloud = FakeICloud()
    icloud.photos.shared_libraries = {"SharedSync-Z": FakeLibrary(recently_deleted=[deleted])}
    monkeypatch.setattr(worker_mod.adapter, "authenticate", lambda *a: icloud)
    monkeypatch.setattr(worker_mod.adapter, "run_sync", lambda *a: 0)
    rc, _ = run_main(
        "sync", _sync_job(photos, shared_lib, library_zone="SharedSync-Z")
    )
    assert rc == 0 and not deleted_path.exists()
    assert list((photos / ".trash" / "shared" / "家庭").rglob("IMG_S_*.HEIC"))


def test_reconcile_trashes_extraneous_and_writes_plan(
    photos_tree: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    photos, lib = photos_tree
    remote = make_asset("R1", "IMG_1.HEIC", TAKEN)
    kept = _touch_expected(remote, lib)
    stray = lib / "2024" / "05" / "stray.HEIC"
    stray.write_bytes(b"old")
    (lib / "2024" / "05" / "abc.part").write_bytes(b"in-flight")
    icloud = FakeICloud()
    icloud.photos.all = [remote]
    monkeypatch.setattr(worker_mod.adapter, "authenticate", lambda *a: icloud)
    plans = tmp_path / "plans"
    rc, events = run_main("reconcile", _sync_job(photos, lib, plan_dir=str(plans)))
    assert rc == 0
    plan_ev = next(e for e in events if e["ev"] == "reconcile_plan")
    assert plan_ev["remote_total"] == 1 and plan_ev["local_total"] == 2
    assert plan_ev["extraneous"] == 1 and plan_ev["fused"] is False
    assert (plans / "run-1.txt").read_text(encoding="utf-8") == "2024/05/stray.HEIC\n"
    assert kept.exists() and not stray.exists()
    assert (lib / "2024" / "05" / "abc.part").exists()
    assert events[-1]["stats"]["trashed"] == 1


def test_reconcile_fuses_large_deletions(
    photos_tree: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    photos, lib = photos_tree
    (lib / "2024" / "05").mkdir(parents=True)
    for i in range(30):
        (lib / "2024" / "05" / f"old_{i}.HEIC").write_bytes(b"x")
    icloud = FakeICloud()
    icloud.photos.all = [make_asset("R1", "IMG_1.HEIC", TAKEN)]
    monkeypatch.setattr(worker_mod.adapter, "authenticate", lambda *a: icloud)
    plans = tmp_path / "plans"
    rc, events = run_main("reconcile", _sync_job(photos, lib, plan_dir=str(plans)))
    assert rc == 6
    plan_ev = events[-1]
    assert plan_ev["ev"] == "reconcile_plan" and plan_ev["fused"] is True
    assert len((plans / "run-1.txt").read_text(encoding="utf-8").splitlines()) == 30
    assert len(list((lib / "2024" / "05").iterdir())) == 30  # nothing moved


def test_reconcile_listing_failure_deletes_nothing(
    photos_tree: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    photos, lib = photos_tree
    stray = lib / "stray.HEIC"
    stray.write_bytes(b"x")

    class ExplodingLibrary:
        @property
        def all(self) -> Any:
            raise RuntimeError("partial listing")

    icloud = FakeICloud()
    monkeypatch.setattr(worker_mod.adapter, "authenticate", lambda *a: icloud)
    monkeypatch.setattr(worker_mod.adapter, "get_library", lambda *a: ExplodingLibrary())
    rc, events = run_main("reconcile", _sync_job(photos, lib, plan_dir=str(tmp_path / "p")))
    assert rc == 1 and events[-1]["ev"] == "error" and stray.exists()


def test_reconcile_apply_plan_needs_no_login(
    photos_tree: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    photos, lib = photos_tree
    (lib / "2024").mkdir()
    (lib / "2024" / "a.HEIC").write_bytes(b"1")
    (lib / "2024" / "b.HEIC").write_bytes(b"2")
    plan = tmp_path / "approved.txt"
    plan.write_text("2024/a.HEIC\n2024/missing.HEIC\n", encoding="utf-8")

    def no_login(*args: Any) -> None:
        raise AssertionError("apply-plan must not log in")

    monkeypatch.setattr(worker_mod.adapter, "authenticate", no_login)
    rc, events = run_main(
        "reconcile", _sync_job(photos, lib), password=None, apply_plan=str(plan)
    )
    assert rc == 0 and events[-1]["stats"] == {"trashed": 1, "planned": 2}
    assert not (lib / "2024" / "a.HEIC").exists() and (lib / "2024" / "b.HEIC").exists()


def test_sync_progress_sampler_reports_counts(
    photos_tree: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    photos, lib = photos_tree
    monkeypatch.setattr(worker_mod, "PROGRESS_INTERVAL_S", 0.01)
    monkeypatch.setattr(worker_mod.adapter, "authenticate", lambda *a: FakeICloud())

    def slow_sync(spec: Any, password: str, exchange: Any, notificator: Any, logger: Any) -> int:
        exchange.get_progress().photos_count = 10
        exchange.get_progress().photos_counter = 3
        logger.info("Downloaded %s", "a.HEIC")
        threading.Event().wait(0.05)
        return 0

    monkeypatch.setattr(worker_mod.adapter, "run_sync", slow_sync)
    rc, events = run_main("sync", _sync_job(photos, lib))
    assert rc == 0
    progress = [e for e in events if e["ev"] == "progress"]
    assert progress and progress[-1]["scanned"] == 3 and progress[-1]["total"] == 10
    assert progress[-1]["downloaded"] == 1


def test_stray_stdout_from_icloudpd_does_not_corrupt_events(
    photos_tree: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    photos, lib = photos_tree
    monkeypatch.setattr(worker_mod.adapter, "authenticate", lambda *a: FakeICloud())

    def chatty_sync(spec: Any, password: str, exchange: Any, notificator: Any, logger: Any) -> int:
        print("icloudpd has saved the photo record to: ./icloudpd-photo-error.json")
        return 0

    monkeypatch.setattr(worker_mod.adapter, "run_sync", chatty_sync)
    rc, events = run_main("sync", _sync_job(photos, lib))
    assert rc == 0 and events[-1]["ev"] == "done"  # every stdout line parsed as JSON
    assert "icloudpd-photo-error" in capsys.readouterr().err


def test_sync_download_phase_unavailable_exits_5(
    photos_tree: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    photos, lib = photos_tree
    monkeypatch.setattr(worker_mod.adapter, "authenticate", lambda *a: FakeICloud())

    def fake_run_sync(
        spec: Any, password: str, exchange: Any, notificator: Any, logger: Any
    ) -> int:
        logger.info(
            PyiCloudServiceUnavailableException(
                "Apple iCloud is temporary refusing to serve icloudpd"
            )
        )
        return 1

    monkeypatch.setattr(worker_mod.adapter, "run_sync", fake_run_sync)
    rc, events = run_main("sync", _sync_job(photos, lib))
    assert rc == 5
    assert events[-1]["ev"] == "error" and events[-1]["kind"] == "unavailable"


def test_reconcile_fuses_on_empty_remote_listing(
    photos_tree: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    photos, lib = photos_tree
    (lib / "2024" / "05").mkdir(parents=True)
    files = [lib / "2024" / "05" / f"f_{i}.HEIC" for i in range(3)]
    for f in files:
        f.write_bytes(b"x")
    icloud = FakeICloud()
    icloud.photos.all = []
    monkeypatch.setattr(worker_mod.adapter, "authenticate", lambda *a: icloud)
    plans = tmp_path / "plans"
    rc, events = run_main("reconcile", _sync_job(photos, lib, plan_dir=str(plans)))
    assert rc == 6
    assert events[-1]["ev"] == "reconcile_plan" and events[-1]["fused"] is True
    assert all(f.exists() for f in files)
    assert len((plans / "run-1.txt").read_text(encoding="utf-8").splitlines()) == 3


def test_run_reports_touchpoint_mismatch_as_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def mismatch() -> None:
        raise RuntimeError("icloudpd 9.9.9 installed")

    monkeypatch.setattr(worker_mod.adapter, "check_touchpoints", mismatch)
    rc, events = run_main("auth", {**BASE, "interactive": True})
    assert rc == 1 and events[-1]["ev"] == "error"


def test_recently_deleted_pass_skips_malformed_asset(
    photos_tree: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    photos, lib = photos_tree
    icloud = FakeICloud()
    icloud.photos.recently_deleted = [object()]
    monkeypatch.setattr(worker_mod.adapter, "authenticate", lambda *a: icloud)
    monkeypatch.setattr(worker_mod.adapter, "run_sync", lambda *a: 0)
    rc, events = run_main("sync", _sync_job(photos, lib))
    assert rc == 0
    assert any(e["ev"] == "warning" for e in events)
    assert events[-1]["ev"] == "done" and events[-1]["stats"]["trashed"] == 0
