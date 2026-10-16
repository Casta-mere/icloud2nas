"""Bridges icloudpd's StatusExchange (MFA hand-off) to the worker's stdin/stdout protocol."""

from __future__ import annotations

import json
import re
import sys
import threading
import time
from collections.abc import Callable
from typing import TextIO

from icloudpd.status import Status, StatusExchange

from icloud2nas.engine.events import Emitter

_BARE_CODE = re.compile(r"^\d{6}$")


class WorkerStatusExchange(StatusExchange):
    """A StatusExchange that reports MFA transitions as events and remembers when the wait began."""

    def __init__(self, emitter: Emitter) -> None:
        super().__init__()
        self._emitter = emitter
        self.mfa_requested_at: float | None = None
        self.mfa_succeeded = False  # a code was accepted by Apple during this exchange's lifetime

    def replace_status(self, expected_status: Status, new_status: Status) -> bool:
        if new_status is Status.NEED_MFA:
            # stamp before the flip so the watchdog never sees NEED_MFA with a stale stamp
            self.mfa_requested_at = time.monotonic()
        changed = super().replace_status(expected_status, new_status)
        if changed and new_status is Status.NEED_MFA:
            self._emitter.emit("need_mfa")
        if (
            changed
            and expected_status is Status.CHECKING_MFA
            and new_status is Status.NO_INPUT_NEEDED
        ):
            self.mfa_requested_at = None
            self.mfa_succeeded = True
            self._emitter.emit("mfa_result", ok=True)
        return changed

    def set_error(self, error: str) -> bool:
        changed = super().set_error(error)
        if changed and self.get_status() is Status.NEED_MFA:
            self.mfa_requested_at = time.monotonic()  # a wrong code restarts the wait window
            self._emitter.emit("mfa_result", ok=False, error=error)
        return changed

    def abandon_mfa_wait(self) -> bool:
        """Leave NEED_MFA so icloudpd's wait loop fails its next transition and raises (no hang)."""
        changed = super().replace_status(Status.NEED_MFA, Status.NO_INPUT_NEEDED)
        if changed:
            self.mfa_requested_at = None
        return changed


def parse_code_line(line: str) -> str | None:
    """Accept {"code": "123456"} or a bare 6-digit code; None if the line is neither."""
    text = line.strip()
    if not text:
        return None
    if _BARE_CODE.match(text):
        return text
    try:
        data = json.loads(text)
    except ValueError:
        return None
    if not isinstance(data, dict) or "code" not in data:
        return None
    code = str(data["code"]).strip()
    return code or None


class StdinCodeFeeder(threading.Thread):
    """Reads code lines from stdin and hands each code to the status exchange."""

    def __init__(
        self,
        exchange: StatusExchange,
        emitter: Emitter,
        stream: TextIO | None = None,
        prompt_grace_s: float = 5.0,
    ) -> None:
        super().__init__(name="stdin-code-feeder", daemon=True)
        self._exchange = exchange
        self._emitter = emitter
        self._stream = stream if stream is not None else sys.stdin
        self._prompt_grace_s = prompt_grace_s

    def _wait_for_prompt(self) -> bool:
        """True once the exchange awaits a code (or already did); False after the grace period."""
        deadline = time.monotonic() + self._prompt_grace_s
        while True:
            if self._exchange.get_status() is Status.NEED_MFA:
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.01)

    def run(self) -> None:
        for raw in self._stream:
            if not raw.strip():
                continue
            code = parse_code_line(raw)
            if code is None:
                self._emitter.emit("warning", message=f"ignored stdin line: {raw.strip()[:40]}")
                continue
            if not self._wait_for_prompt() or not self._exchange.set_payload(code):
                self._emitter.emit("warning", message="code ignored: not waiting for MFA")


class MfaWatchdog(threading.Thread):
    """Calls on_timeout once if the exchange keeps waiting for a code longer than timeout_s."""

    def __init__(
        self,
        exchange: WorkerStatusExchange,
        timeout_s: float,
        on_timeout: Callable[[], None],
        poll_s: float = 1.0,
    ) -> None:
        super().__init__(name="mfa-watchdog", daemon=True)
        self._exchange = exchange
        self._timeout_s = timeout_s
        self._on_timeout = on_timeout
        self._poll_s = poll_s
        self._stop_event = threading.Event()

    def stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        while not self._stop_event.wait(self._poll_s):
            started = self._exchange.mfa_requested_at
            if (
                started is not None
                and self._exchange.get_status() is Status.NEED_MFA
                and time.monotonic() - started > self._timeout_s
            ):
                self._on_timeout()
                return
