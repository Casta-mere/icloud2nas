from __future__ import annotations

import io
import json
import logging
import threading
from collections.abc import Callable
from typing import Any

import pytest
from icloudpd.status import Status, StatusExchange
from pyicloud_ipd.exceptions import PyiCloudFailedLoginException

from icloud2nas import worker as worker_mod
from icloud2nas.engine.errors import ExitCode, HardExit
from icloud2nas.engine.job import JOB_ENV, PASSWORD_ENV
from tests.fakes import FakeICloud, FakeLibrary

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
    logger, counter = worker_mod.setup_logging()
    logger.info("Downloaded %s", "x.HEIC")
    logger.info("Downloaded %s", "y.MOV")
    logger.info("%s already exists", "x.HEIC")
    assert counter.downloaded == 2
    assert logger.name == "icloudpd" and logger.level == logging.INFO
