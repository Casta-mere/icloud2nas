"""Worker process: one job (auth | list | sync | reconcile); events on stdout, result in exit code.

Protocol (spec §3.3): argv `--mode M --run-id R [--apply-plan FILE]`; env I2N_JOB (JSON) and
I2N_PASSWORD; stdin receives {"code": "123456"} lines while an interactive login waits for 2FA.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import threading
from collections.abc import Callable, Mapping, Sequence
from datetime import date
from typing import TextIO

from icloud2nas.engine import icloudpd_adapter as adapter
from icloud2nas.engine.errors import ExitCode, HardExit, classify
from icloud2nas.engine.events import Emitter
from icloud2nas.engine.job import MODES, PASSWORD_ENV, JobSpec
from icloud2nas.engine.mfa import MfaWatchdog, StdinCodeFeeder, WorkerStatusExchange
from icloud2nas.engine.paths import expected_paths, walk_library
from icloud2nas.engine.reconcile import apply_paths, compute_plan, read_plan, write_plan
from icloud2nas.engine.trash import move_to_trash

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


class LastExceptionRecorder(logging.Handler):
    """Remembers the last exception object icloudpd logged (core_single_run: logger.info(error))."""

    def __init__(self) -> None:
        super().__init__()
        self.last: BaseException | None = None

    def emit(self, record: logging.LogRecord) -> None:
        if isinstance(record.msg, BaseException):
            self.last = record.msg


def setup_logging(
    level: int = logging.INFO,
) -> tuple[logging.Logger, DownloadCounter, LastExceptionRecorder]:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-8s %(message)s", "%Y-%m-%d %H:%M:%S")
    )
    counter = DownloadCounter()
    recorder = LastExceptionRecorder()
    logger = logging.getLogger(ICLOUDPD_LOGGER)
    logger.handlers.clear()
    logger.setLevel(level)
    logger.propagate = False
    logger.addHandler(handler)
    logger.addHandler(counter)
    logger.addHandler(recorder)
    pyicloud = logging.getLogger("pyicloud_ipd")
    pyicloud.handlers.clear()
    pyicloud.setLevel(level)
    pyicloud.propagate = False
    pyicloud.addHandler(handler)
    return logger, counter, recorder


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
        self.logger, self.counter, self.recorder = setup_logging()

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
        # icloudpd print()s on some error paths; keep the event stream clean
        sys.stdout = sys.stderr
        os.environ["CLIENT_ID"] = self.spec.client_id
        watchdog: MfaWatchdog | None = None
        if self.spec.interactive:
            StdinCodeFeeder(self.exchange, self.emitter, self.stdin).start()
            watchdog = MfaWatchdog(self.exchange, self.spec.mfa_timeout_s, self._on_mfa_timeout)
            watchdog.start()
        try:
            adapter.check_touchpoints()
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

    def _login(self) -> adapter.PyiCloudService:
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
            adapter.wait_for_photos_index(icloud, self.logger, self.spec.index_wait_s)
            personal, shared = adapter.list_libraries(icloud)
            self.emitter.emit("libraries", personal=personal, shared=shared)
            self.emitter.emit("done", stats={"personal": len(personal), "shared": len(shared)})
            return ExitCode.OK
        if mode == "sync":
            return self._sync()
        if mode == "reconcile":
            return self._reconcile()
        raise ValueError(f"unknown mode {mode!r}")

    # -- sync ---------------------------------------------------------------------------------

    def _sync(self) -> ExitCode:
        icloud = self._login()  # classifies password/MFA trouble before icloudpd's own retry loop
        adapter.wait_for_photos_index(icloud, self.logger, self.spec.index_wait_s)
        library = adapter.get_library(icloud, self.spec.library_zone)
        stop = threading.Event()
        sampler = threading.Thread(target=self._sample_progress, args=(stop,), daemon=True)
        sampler.start()
        try:
            result = adapter.run_sync(
                self.spec,
                self.password,
                self.exchange,
                self.notificator,
                self.logger,
                icloud=icloud,
            )
        finally:
            stop.set()
            sampler.join(timeout=5)
        if result != 0:
            logged = self.recorder.last
            if logged is not None:
                code, kind = classify(logged)
                self.emitter.emit("error", kind=kind, message=str(logged))
                return code
            self.emitter.emit(
                "error", kind="icloudpd", message=f"icloudpd returned {result}, see run log"
            )
            return ExitCode.FAILED
        trashed = self._recently_deleted_pass(library)
        self.emitter.emit(
            "done",
            stats={
                "downloaded": self.counter.downloaded,
                "trashed": trashed,
                "full": self.spec.full,
            },
        )
        return ExitCode.OK

    def _sample_progress(self, stop: threading.Event) -> None:
        while not stop.wait(PROGRESS_INTERVAL_S):
            progress = self.exchange.get_progress()
            self.emitter.emit(
                "progress",
                downloaded=self.counter.downloaded,
                scanned=progress.photos_counter,
                total=progress.photos_count,
                last=progress.photos_last_message,
            )

    def _recently_deleted_pass(self, library: adapter.PhotoLibrary) -> int:
        """Trash local copies of assets Apple lists under Recently Deleted.

        Replaces icloudpd's --auto-delete, whose path matching ignores the name-id7 policy.
        """
        spec = self.spec
        assert spec.target_dir and spec.photos_root
        moved = 0
        for asset in adapter.iter_recently_deleted(library):
            try:
                candidates = expected_paths(asset, spec.target_dir, spec.policy)
            except Exception as exc:  # noqa: BLE001 - a bad record must not fail a finished sync
                self.emitter.emit(
                    "warning",
                    message=f"skipping recently-deleted asset {getattr(asset, 'id', '?')}: {exc}",
                )
                continue
            for path in candidates:
                if not os.path.isfile(path):
                    continue
                if spec.dry_run:
                    self.logger.info("[DRY RUN] would trash %s", path)
                else:
                    move_to_trash(
                        spec.photos_root, spec.library_rel, spec.target_dir, path, self.today()
                    )
                moved += 1
        return moved

    # -- reconcile ----------------------------------------------------------------------------

    def _reconcile(self) -> ExitCode:
        spec = self.spec
        assert spec.target_dir and spec.photos_root
        if spec.apply_plan:
            rel_paths = read_plan(spec.apply_plan)
            moved = (
                0
                if spec.dry_run
                else apply_paths(
                    rel_paths, spec.target_dir, spec.photos_root, spec.library_rel, self.today()
                )
            )
            self.emitter.emit("done", stats={"trashed": moved, "planned": len(rel_paths)})
            return ExitCode.OK
        assert spec.plan_dir
        icloud = self._login()
        adapter.wait_for_photos_index(icloud, self.logger, spec.index_wait_s)
        library = adapter.get_library(icloud, spec.library_zone)
        expected: set[str] = set()
        remote_total = 0
        for asset in adapter.iter_all(library):  # any exception here aborts before any delete
            expected |= expected_paths(asset, spec.target_dir, spec.policy)
            remote_total += 1
            if remote_total % 1000 == 0:
                self.emitter.emit("progress", scanned=remote_total)
        local = walk_library(spec.target_dir)
        plan = compute_plan(expected, local, spec.target_dir, remote_total)
        plan_file = os.path.join(spec.plan_dir, f"{spec.run_id}.txt")
        write_plan(plan, plan_file)
        self.emitter.emit(
            "reconcile_plan",
            remote_total=plan.remote_total,
            local_total=plan.local_total,
            extraneous=len(plan.extraneous),
            missing=len(expected - local),
            plan_file=plan_file,
            fused=plan.fused,
        )
        if plan.fused:
            return ExitCode.FUSED
        moved = (
            0
            if spec.dry_run
            else apply_paths(
                plan.extraneous, spec.target_dir, spec.photos_root, spec.library_rel, self.today()
            )
        )
        self.emitter.emit(
            "done",
            stats={"trashed": moved, "remote_total": remote_total, "local_total": plan.local_total},
        )
        return ExitCode.OK


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
