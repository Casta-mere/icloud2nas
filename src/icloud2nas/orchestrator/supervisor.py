"""Drive one worker subprocess over docs/worker-protocol.md (argv + env in, JSON events out)."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from icloud2nas.orchestrator.config import Config

log = logging.getLogger(__name__)
TERMINAL_EVENTS = ("done", "error", "reconcile_plan")
KILL_GRACE_S = 5.0
STDOUT_LINE_LIMIT = 16 * 1024 * 1024  # one event line; longer is a protocol violation


@dataclass
class WorkerResult:
    exit_code: int
    events: list[dict[str, Any]] = field(default_factory=list)
    terminal: dict[str, Any] | None = None
    killed: bool = False
    killed_by: str | None = None  # "timeout" | "shutdown" | "protocol" when killed


class WorkerProcess:
    def __init__(
        self,
        config: Config,
        *,
        run_id: str,
        mode: str,
        job: dict[str, Any],
        password: str | None,
        interactive: bool,
        log_path: Path,
        apply_plan: str | None = None,
        on_event: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self._config = config
        self.run_id = run_id
        self.mode = mode
        self._job = job
        self._password = password
        self._interactive = interactive
        self._log_path = log_path
        self._apply_plan = apply_plan
        self._on_event = on_event
        self._proc: asyncio.subprocess.Process | None = None
        self._reader: asyncio.Task[None] | None = None
        self._events: list[dict[str, Any]] = []
        self._terminal: dict[str, Any] | None = None
        self._log_file: Any = None
        self._killed = False
        self._killed_by: str | None = None
        self._result: WorkerResult | None = None

    @property
    def pid(self) -> int | None:
        return self._proc.pid if self._proc is not None else None

    async def start(self) -> None:
        env = dict(os.environ)
        env["I2N_JOB"] = json.dumps(self._job, ensure_ascii=False)
        if self._password is not None:
            env["I2N_PASSWORD"] = self._password
        else:
            env.pop("I2N_PASSWORD", None)
        argv = [*self._config.worker_cmd, "--mode", self.mode, "--run-id", self.run_id]
        if self._apply_plan:
            argv += ["--apply-plan", self._apply_plan]
        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        self._log_file = open(self._log_path, "ab")  # noqa: SIM115 - closed in wait()
        try:
            self._proc = await asyncio.create_subprocess_exec(
                *argv,
                env=env,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=self._log_file,
                limit=STDOUT_LINE_LIMIT,
            )
        except BaseException:
            self._log_file.close()
            self._log_file = None
            raise
        if not self._interactive and self._proc.stdin is not None:
            self._proc.stdin.close()  # non-interactive runs never read stdin (spec §3.3a)
        self._reader = asyncio.create_task(self._read_events())

    async def _read_events(self) -> None:
        assert self._proc is not None and self._proc.stdout is not None
        while True:
            try:
                line = await self._proc.stdout.readline()
            except ValueError:  # line longer than STDOUT_LINE_LIMIT
                log.error("worker %s: stdout line over %d bytes; killing", self.run_id,
                          STDOUT_LINE_LIMIT)
                await self.kill(reason="protocol")
                return
            if not line:
                return
            try:
                event = json.loads(line)
            except ValueError:
                log.warning("worker %s: non-JSON stdout line ignored: %r", self.run_id, line[:120])
                continue
            if not isinstance(event, dict) or "ev" not in event:
                continue
            self._events.append(event)
            if event["ev"] in TERMINAL_EVENTS and not (
                event["ev"] == "reconcile_plan" and self._terminal is not None
            ):
                self._terminal = event
            if self._on_event is not None:
                try:
                    self._on_event(event)
                except Exception:  # noqa: BLE001 - a callback bug must not stop the reader
                    log.exception("worker %s: on_event failed", self.run_id)

    async def send_code(self, code: str) -> None:
        if self._proc is None or self._proc.stdin is None or self._proc.stdin.is_closing():
            raise RuntimeError("worker stdin is closed (non-interactive run or process ended)")
        self._proc.stdin.write((json.dumps({"code": code}) + "\n").encode("utf-8"))
        await self._proc.stdin.drain()

    async def kill(self, reason: str = "shutdown") -> None:
        if self._proc is None or self._proc.returncode is not None:
            return
        self._killed = True
        self._killed_by = reason
        self._proc.send_signal(signal.SIGTERM)
        try:
            await asyncio.wait_for(self._proc.wait(), KILL_GRACE_S)
        except TimeoutError:
            self._proc.kill()
            await self._proc.wait()

    async def wait(self, timeout_s: float | None = None) -> WorkerResult:
        """Wait for exit (killing on timeout). Idempotent: later calls return the first result."""
        if self._result is not None:
            return self._result
        assert self._proc is not None and self._reader is not None
        try:
            await asyncio.wait_for(self._reader, timeout_s)
            await self._proc.wait()
        except TimeoutError:
            await self.kill(reason="timeout")
            self._reader.cancel()
        finally:
            if self._proc.stdin is not None and not self._proc.stdin.is_closing():
                self._proc.stdin.close()
            if self._log_file is not None:
                self._log_file.close()
        code = self._proc.returncode if self._proc.returncode is not None else -1
        self._result = WorkerResult(
            exit_code=code,
            events=list(self._events),
            terminal=self._terminal,
            killed=self._killed,
            killed_by=self._killed_by,
        )
        return self._result


async def run_worker(
    config: Config,
    *,
    run_id: str,
    mode: str,
    job: dict[str, Any],
    password: str | None,
    interactive: bool,
    log_path: Path,
    apply_plan: str | None = None,
    on_event: Callable[[dict[str, Any]], None] | None = None,
    timeout_s: float | None = None,
) -> WorkerResult:
    proc = WorkerProcess(
        config, run_id=run_id, mode=mode, job=job, password=password, interactive=interactive,
        log_path=log_path, apply_plan=apply_plan, on_event=on_event,
    )
    await proc.start()
    return await proc.wait(timeout_s)
