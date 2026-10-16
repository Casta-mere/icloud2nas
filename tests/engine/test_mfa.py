import io
import json
import threading
import time

from icloudpd.status import Status

from icloud2nas.engine.events import Emitter
from icloud2nas.engine.mfa import MfaWatchdog, StdinCodeFeeder, WorkerStatusExchange


def _events(buf: io.StringIO) -> list[dict[str, object]]:
    return [json.loads(line) for line in buf.getvalue().splitlines()]


def test_status_exchange_emits_mfa_lifecycle() -> None:
    buf = io.StringIO()
    ex = WorkerStatusExchange(Emitter(buf))
    assert ex.replace_status(Status.NO_INPUT_NEEDED, Status.NEED_MFA)
    assert ex.mfa_requested_at is not None
    assert ex.set_payload("123456")
    assert ex.replace_status(Status.SUPPLIED_MFA, Status.CHECKING_MFA)
    assert ex.set_error("Failed to verify two-factor authentication code")
    assert ex.get_status() is Status.NEED_MFA
    assert ex.set_payload("654321")
    assert ex.replace_status(Status.SUPPLIED_MFA, Status.CHECKING_MFA)
    assert ex.replace_status(Status.CHECKING_MFA, Status.NO_INPUT_NEEDED)
    assert ex.mfa_requested_at is None
    assert [(e["ev"], e.get("ok")) for e in _events(buf)] == [
        ("need_mfa", None),
        ("mfa_result", False),
        ("mfa_result", True),
    ]


def test_feeder_accepts_json_and_bare_codes_and_warns_otherwise() -> None:
    buf = io.StringIO()
    emitter = Emitter(buf)
    ex = WorkerStatusExchange(emitter)
    ex.replace_status(Status.NO_INPUT_NEEDED, Status.NEED_MFA)
    feeder = StdinCodeFeeder(ex, emitter, io.StringIO('garbage\n{"code": " 123456 "}\n'))
    feeder.start()
    feeder.join(2)
    assert ex.get_payload() == "123456"
    assert [e["ev"] for e in _events(buf)] == ["need_mfa", "warning"]

    buf2 = io.StringIO()
    emitter2 = Emitter(buf2)
    ex2 = WorkerStatusExchange(emitter2)
    ex2.replace_status(Status.NO_INPUT_NEEDED, Status.NEED_MFA)
    StdinCodeFeeder(ex2, emitter2, io.StringIO("654321\n")).run()
    assert ex2.get_payload() == "654321"


def test_feeder_warns_when_nothing_is_waiting() -> None:
    buf = io.StringIO()
    emitter = Emitter(buf)
    ex = WorkerStatusExchange(emitter)
    StdinCodeFeeder(ex, emitter, io.StringIO('{"code": "123456"}\n'), prompt_grace_s=0).run()
    events = _events(buf)
    assert events[-1]["ev"] == "warning" and "not waiting" in str(events[-1]["message"])


def test_feeder_keeps_a_code_pasted_slightly_early() -> None:
    buf = io.StringIO()
    emitter = Emitter(buf)
    ex = WorkerStatusExchange(emitter)
    feeder = StdinCodeFeeder(ex, emitter, io.StringIO("123456\n"), prompt_grace_s=2.0)
    feeder.start()
    time.sleep(0.05)
    ex.replace_status(Status.NO_INPUT_NEEDED, Status.NEED_MFA)
    feeder.join(3)
    assert ex.get_payload() == "123456"


def test_abandon_mfa_wait_unblocks_only_when_waiting() -> None:
    buf = io.StringIO()
    ex = WorkerStatusExchange(Emitter(buf))
    assert ex.abandon_mfa_wait() is False
    ex.replace_status(Status.NO_INPUT_NEEDED, Status.NEED_MFA)
    assert ex.abandon_mfa_wait() is True
    assert ex.mfa_requested_at is None
    assert ex.get_status() is Status.NO_INPUT_NEEDED
    assert ex.replace_status(Status.SUPPLIED_MFA, Status.CHECKING_MFA) is False  # icloudpd raises
    assert [e["ev"] for e in _events(buf)] == ["need_mfa"]


def test_watchdog_fires_once_after_timeout() -> None:
    buf = io.StringIO()
    ex = WorkerStatusExchange(Emitter(buf))
    fired = threading.Event()
    dog = MfaWatchdog(ex, timeout_s=0.05, on_timeout=fired.set, poll_s=0.01)
    dog.start()
    time.sleep(0.1)
    assert not fired.is_set()  # nothing requested yet
    ex.replace_status(Status.NO_INPUT_NEEDED, Status.NEED_MFA)
    assert fired.wait(1.0)
    dog.join(1.0)
    assert not dog.is_alive()


def test_watchdog_stops_quietly_when_code_arrives() -> None:
    buf = io.StringIO()
    ex = WorkerStatusExchange(Emitter(buf))
    fired = threading.Event()
    dog = MfaWatchdog(ex, timeout_s=0.2, on_timeout=fired.set, poll_s=0.01)
    dog.start()
    ex.replace_status(Status.NO_INPUT_NEEDED, Status.NEED_MFA)
    ex.set_payload("123456")
    ex.replace_status(Status.SUPPLIED_MFA, Status.CHECKING_MFA)
    ex.replace_status(Status.CHECKING_MFA, Status.NO_INPUT_NEEDED)
    time.sleep(0.3)
    dog.stop()
    dog.join(1.0)
    assert not fired.is_set()


def test_wrong_code_restarts_the_wait_window() -> None:
    buf = io.StringIO()
    ex = WorkerStatusExchange(Emitter(buf))
    ex.replace_status(Status.NO_INPUT_NEEDED, Status.NEED_MFA)
    first = ex.mfa_requested_at
    assert first is not None
    time.sleep(0.02)
    ex.set_payload("000000")
    ex.replace_status(Status.SUPPLIED_MFA, Status.CHECKING_MFA)
    assert ex.set_error("bad code")
    assert ex.mfa_requested_at is not None and ex.mfa_requested_at > first


def test_set_error_outside_mfa_emits_nothing() -> None:
    buf = io.StringIO()
    ex = WorkerStatusExchange(Emitter(buf))
    ex.replace_status(Status.NO_INPUT_NEEDED, Status.NEED_PASSWORD)
    ex.set_payload("pw")
    ex.replace_status(Status.SUPPLIED_PASSWORD, Status.CHECKING_PASSWORD)
    assert ex.set_error("bad password")
    assert ex.get_status() is Status.NO_INPUT_NEEDED
    assert [e["ev"] for e in _events(buf)] == []


def test_mfa_succeeded_flag_set_only_on_accepted_code() -> None:
    buf = io.StringIO()
    ex = WorkerStatusExchange(Emitter(buf))
    assert ex.mfa_succeeded is False
    ex.replace_status(Status.NO_INPUT_NEEDED, Status.NEED_MFA)
    ex.set_payload("000000")
    ex.replace_status(Status.SUPPLIED_MFA, Status.CHECKING_MFA)
    ex.set_error("bad code")
    assert ex.mfa_succeeded is False
    ex.set_payload("123456")
    ex.replace_status(Status.SUPPLIED_MFA, Status.CHECKING_MFA)
    ex.replace_status(Status.CHECKING_MFA, Status.NO_INPUT_NEEDED)
    assert ex.mfa_succeeded is True
