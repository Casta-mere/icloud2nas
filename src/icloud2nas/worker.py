"""Worker process: one job (auth | list | sync | reconcile); events on stdout, result in exit code.

Protocol (spec §3.3): argv `--mode M --run-id R [--apply-plan FILE]`; env I2N_JOB (JSON) and
I2N_PASSWORD; stdin receives {"code": "123456"} lines while an interactive login waits for 2FA.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from collections.abc import Callable, Mapping, Sequence
from datetime import date
from typing import TextIO

from icloud2nas.engine import icloudpd_adapter as adapter
from icloud2nas.engine.errors import ExitCode, HardExit, classify
from icloud2nas.engine.events import Emitter
from icloud2nas.engine.job import MODES, PASSWORD_ENV, JobSpec
from icloud2nas.engine.mfa import MfaWatchdog, StdinCodeFeeder, WorkerStatusExchange

PROGRESS_INTERVAL_S = 60.0
ICLOUDPD_LOGGER = "icloudpd"


def hard_exit(code: ExitCode) -> None:
    """Terminate immediately without Python cleanup (used from inside icloudpd callbacks)."""
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(int(code))


class DownloadCounter(logging.Handler):
    """Counts icloudpd's 'Downloaded ...' log lines; the only cheap signal icloudpd gives us."""

    def __init__(self) -> None:
        super().__init__()
        self.downloaded = 0

    def emit(self, record: logging.LogRecord) -> None:
        if isinstance(record.msg, str) and record.msg.startswith("Downloaded "):
            self.downloaded += 1


def setup_logging(level: int = logging.INFO) -> tuple[logging.Logger, DownloadCounter]:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-8s %(message)s", "%Y-%m-%d %H:%M:%S")
    )
    counter = DownloadCounter()
    logger = logging.getLogger(ICLOUDPD_LOGGER)
    logger.handlers.clear()
    logger.setLevel(level)
    logger.propagate = False
    logger.addHandler(handler)
    logger.addHandler(counter)
    pyicloud = logging.getLogger("pyicloud_ipd")
    pyicloud.handlers.clear()
    pyicloud.setLevel(level)
    pyicloud.propagate = False
    pyicloud.addHandler(handler)
    return logger, counter


class Worker:
    def __init__(
        self,
        spec: JobSpec,
        password: str,
        emitter: Emitter,
        *,
        exit_hook: Callable[[ExitCode], None] = hard_exit,
        stdin: TextIO | None = None,
        today: Callable[[], date] = date.today,
    ) -> None:
        self.spec = spec
        self.password = password
        self.emitter = emitter
        self.exit_hook = exit_hook
        self.stdin = stdin
        self.today = today
        self.mfa_timed_out = False
        self.exchange = WorkerStatusExchange(emitter)
        self.logger, self.counter = setup_logging()

    # -- icloudpd callback -------------------------------------------------------------------

    def notificator(self) -> None:
        """icloudpd calls this when Apple asks for a 2FA code, before it starts waiting."""
        if self.spec.interactive:
            return  # WorkerStatusExchange emits need_mfa as soon as icloudpd flips to NEED_MFA
        self.emitter.emit("need_mfa")
        self.exit_hook(ExitCode.NEED_MFA)

    # -- lifecycle ---------------------------------------------------------------------------

    def run(self) -> ExitCode:
        self.emitter.emit("started", mode=self.spec.mode, pid=os.getpid(), run_id=self.spec.run_id)
        os.environ["CLIENT_ID"] = self.spec.client_id
        watchdog: MfaWatchdog | None = None
        if self.spec.interactive:
            StdinCodeFeeder(self.exchange, self.emitter, self.stdin).start()
            watchdog = MfaWatchdog(self.exchange, self.spec.mfa_timeout_s, self._on_mfa_timeout)
            watchdog.start()
        try:
            return self._dispatch()
        except HardExit as exc:
            return exc.code
        except Exception as exc:  # noqa: BLE001 - every failure must become an event + exit code
            if self.mfa_timed_out:
                return ExitCode.FAILED  # the mfa_timeout error event was already emitted
            code, kind = classify(exc)
            self.logger.exception("worker failed")
            self.emitter.emit("error", kind=kind, message=str(exc))
            return code
        finally:
            if watchdog is not None:
                watchdog.stop()

    def _on_mfa_timeout(self) -> None:
        """Watchdog thread: report, then make icloudpd's wait loop raise in the main thread."""
        self.mfa_timed_out = True
        self.emitter.emit(
            "error", kind="mfa_timeout", message=f"no code within {self.spec.mfa_timeout_s}s"
        )
        self.exchange.abandon_mfa_wait()

    def _login(self) -> adapter.PyiCloudService:  # type: ignore[name-defined]
        return adapter.authenticate(
            self.spec, self.password, self.exchange, self.notificator, self.logger
        )

    def _dispatch(self) -> ExitCode:
        mode = self.spec.mode
        if mode == "auth":
            self._login()
            self.emitter.emit("done", stats={})
            return ExitCode.OK
        if mode == "list":
            icloud = self._login()
            personal, shared = adapter.list_libraries(icloud)
            self.emitter.emit("libraries", personal=personal, shared=shared)
            self.emitter.emit("done", stats={"personal": len(personal), "shared": len(shared)})
            return ExitCode.OK
        if mode == "sync":
            return self._sync()
        if mode == "reconcile":
            return self._reconcile()
        raise ValueError(f"unknown mode {mode!r}")

    def _sync(self) -> ExitCode:
        raise NotImplementedError("Task 11")

    def _reconcile(self) -> ExitCode:
        raise NotImplementedError("Task 11")


# -- entry point ------------------------------------------------------------------------------


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="icloud2nas-worker", description=__doc__)
    parser.add_argument("--mode", required=True, choices=MODES)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--apply-plan", help="reconcile only: execute this approved plan file")
    return parser.parse_args(argv)


def main(
    argv: Sequence[str],
    *,
    env: Mapping[str, str] | None = None,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
    exit_hook: Callable[[ExitCode], None] = hard_exit,
) -> int:
    args = parse_args(argv)
    emitter = Emitter(stdout)
    source = os.environ if env is None else env
    try:
        spec = JobSpec.from_env(args.mode, args.run_id, args.apply_plan, source)
    except (ValueError, TypeError) as exc:
        emitter.emit("error", kind="config", message=str(exc))
        return int(ExitCode.FAILED)
    password = source.get(PASSWORD_ENV, "")
    needs_login = not (spec.mode == "reconcile" and spec.apply_plan)
    if needs_login and not password:
        emitter.emit("error", kind="config", message=f"{PASSWORD_ENV} is not set")
        return int(ExitCode.FAILED)
    return int(Worker(spec, password, emitter, exit_hook=exit_hook, stdin=stdin).run())


def cli() -> None:
    sys.exit(main(sys.argv[1:]))


if __name__ == "__main__":
    cli()
