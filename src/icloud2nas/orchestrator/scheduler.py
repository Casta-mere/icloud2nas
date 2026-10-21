"""Scheduler (spec §7): decide what is due, launch workers under locks, chain nightly."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import random
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from icloud2nas.orchestrator.bus import EventBus
from icloud2nas.orchestrator.config import Config
from icloud2nas.orchestrator.jobs import build_job, validate_job
from icloud2nas.orchestrator.onboarding import Onboarding
from icloud2nas.orchestrator.runs import RunManager
from icloud2nas.orchestrator.settings import Settings
from icloud2nas.orchestrator.store import repo
from icloud2nas.orchestrator.store.db import Store
from icloud2nas.orchestrator.store.models import Library, utcnow
from icloud2nas.orchestrator.store.secrets import Secrets
from icloud2nas.orchestrator.supervisor import WorkerProcess, WorkerResult

log = logging.getLogger(__name__)
RUN_TIMEOUT_S = 12 * 3600
KIND_ORDER = {"nightly": 0, "reconcile": 1, "full": 2, "quick": 3}


@dataclass(frozen=True)
class DueJob:
    library_id: str
    account_id: str
    kind: str  # quick | full | nightly | reconcile
    trigger: str  # schedule | nightly | manual


def local_now(now: datetime, tz: str) -> datetime:
    return now.astimezone(ZoneInfo(tz))


def nightly_due(library: Library, now_local: datetime) -> bool:
    return (
        now_local.strftime("%H:%M") >= library.nightly_at
        and library.last_nightly_on != now_local.date()
    )


class Scheduler:
    def __init__(
        self,
        config: Config,
        store: Store,
        secrets: Secrets,
        bus: EventBus,
        runs: RunManager,
        onboarding: Onboarding,
        settings: Settings,
        *,
        clock: Callable[[], datetime] = utcnow,
        jitter_s: tuple[int, int] = (0, 300),
        tick_s: float = 15.0,
    ) -> None:
        self.config = config
        self.store = store
        self.secrets = secrets
        self.bus = bus
        self.runs = runs
        self.onboarding = onboarding
        self.settings = settings
        self.clock = clock
        self.jitter_s = jitter_s
        self.tick_s = tick_s
        self.running_libraries: set[str] = set()
        self.running_accounts: set[str] = set()
        self.active: dict[str, WorkerProcess] = {}
        self.tasks: set[asyncio.Task[None]] = set()
        self._space_low = False

    # -- policy --------------------------------------------------------------------------------

    def paused(self) -> bool:
        return bool(self.settings.get("paused"))

    def free_space_ok(self) -> bool:
        try:
            free = shutil.disk_usage(self.config.photos_dir).free
        except FileNotFoundError:
            return True  # photos dir not mounted yet: let the worker fail loudly instead
        return bool(free >= self.settings.get("min_free_gb") * 1024**3)

    def _apply_space_state(self) -> bool:
        ok = self.free_space_ok()
        if ok == (not self._space_low):
            return ok
        with self.store.session() as s:
            for library in repo.list_libraries(s):
                if not ok and library.status == "ok":
                    library.status = "space_paused"
                elif ok and library.status == "space_paused":
                    library.status = "ok"
        self._space_low = not ok
        topic = "space.low" if not ok else "space.ok"
        self.bus.publish(topic, min_free_gb=self.settings.get("min_free_gb"))
        return ok

    def due_jobs(self, now: datetime | None = None) -> list[DueJob]:
        now = now or self.clock()
        if self.paused() or not self._apply_space_state():
            return []
        now_local = local_now(now, self.config.tz)
        jobs: list[tuple[int, datetime, DueJob]] = []
        with self.store.session() as s:
            accounts = {a.id: a for a in repo.list_accounts(s)}
            for library in repo.list_libraries(s, enabled_only=True):
                account = accounts.get(library.account_id)
                if (
                    library.kind != "personal"
                    or library.status != "ok"
                    or account is None
                    or account.status != "ok"
                    or (account.backoff_until is not None and account.backoff_until > now)
                ):
                    continue
                if library.last_success_at is None:
                    job = DueJob(library.id, account.id, "full", "schedule")
                elif nightly_due(library, now_local):
                    job = DueJob(library.id, account.id, "nightly", "nightly")
                elif now - library.last_success_at >= timedelta(minutes=library.interval_min):
                    job = DueJob(library.id, account.id, "quick", "schedule")
                else:
                    continue
                jobs.append(
                    (
                        KIND_ORDER[job.kind],
                        library.last_success_at or datetime.min.replace(tzinfo=now.tzinfo),
                        job,
                    )
                )
        jobs.sort(key=lambda item: (item[0], item[1]))
        return [job for _, _, job in jobs]

    def request_manual(self, library_id: str, mode: str) -> DueJob:
        with self.store.session() as s:
            library = repo.get_library(s, library_id)
            if library is None:
                raise KeyError(library_id)
            account_id = library.account_id
        kind = "full" if mode == "sync" else "reconcile"
        return DueJob(library_id, account_id, kind, "manual")

    # -- launching -----------------------------------------------------------------------------

    def can_launch(self, job: DueJob) -> bool:
        return (
            job.library_id not in self.running_libraries
            and job.account_id not in self.running_accounts
            and len(self.running_libraries) < self.settings.get("max_workers")
        )

    async def launch(self, job: DueJob) -> str:
        if not self.can_launch(job):
            raise RuntimeError("busy")
        self.running_libraries.add(job.library_id)
        self.running_accounts.add(job.account_id)
        first_mode = "reconcile" if job.kind == "reconcile" else "sync"
        try:
            run_id = self.runs.start_run(
                account_id=job.account_id,
                library_id=job.library_id,
                mode=first_mode,
                trigger=job.trigger,
            )
        except Exception:
            self.running_libraries.discard(job.library_id)
            self.running_accounts.discard(job.account_id)
            raise
        task = asyncio.create_task(self._run_chain(job, run_id))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return run_id

    async def _run_one(self, job: DueJob, run_id: str, mode: str, full: bool) -> int:
        try:
            with self.store.session() as s:
                account = repo.get_account(s, job.account_id)
                library = repo.get_library(s, job.library_id)
                assert account is not None and library is not None
                spec = build_job(
                    self.config,
                    account,
                    library,
                    interactive=False,
                    full=full,
                    policy=self.settings.policy(),
                )
                password = self.onboarding.password_for(account)
            validate_job(mode, spec)
        except Exception as exc:
            self.runs.finish_run(
                run_id,
                WorkerResult(
                    exit_code=1,
                    events=[],
                    terminal={"ev": "error", "kind": "internal", "message": str(exc)},
                ),
            )
            raise

        def register(process: WorkerProcess) -> None:
            self.active[run_id] = process

        try:
            result = await self.runs.execute(
                run_id,
                mode=mode,
                job=spec,
                password=password,
                interactive=False,
                timeout_s=RUN_TIMEOUT_S,
                on_start=register,
            )
        finally:
            self.active.pop(run_id, None)
        return result.exit_code

    async def _run_chain(self, job: DueJob, run_id: str) -> None:
        started_on = local_now(self.clock(), self.config.tz).date()  # the night this run belongs to
        try:
            if job.kind == "reconcile":
                await self._run_one(job, run_id, "reconcile", full=False)
                return
            code: int | None = None
            try:
                code = await self._run_one(
                    job, run_id, "sync", full=job.kind in ("full", "nightly")
                )
            finally:
                if job.kind == "nightly" or (
                    job.kind == "full" and job.trigger == "schedule" and code == 0
                ):
                    # nightly: once per night whatever the outcome; first-ever (scheduled) full
                    # sync: skip tonight's redundant nightly; a manual full never stamps
                    with self.store.session() as s:
                        library = repo.get_library(s, job.library_id)
                        if library is not None:
                            library.last_nightly_on = started_on
            if job.kind == "nightly" and code == 0:
                chained = self.runs.start_run(
                    account_id=job.account_id,
                    library_id=job.library_id,
                    mode="reconcile",
                    trigger="chain",
                )
                await self._run_one(job, chained, "reconcile", full=False)
        except Exception:  # noqa: BLE001 - a launch bug must release the locks and be logged
            log.exception("job %s on library %s crashed", job.kind, job.library_id)
        finally:
            self.running_libraries.discard(job.library_id)
            self.running_accounts.discard(job.account_id)

    async def tick(self) -> int:
        launched = 0
        for job in self.due_jobs():
            if self.can_launch(job):
                await self.launch(job)
                launched += 1
        return launched

    # -- daemon --------------------------------------------------------------------------------

    async def run_forever(self, stop: asyncio.Event) -> None:
        with self.store.session() as s:
            swept = repo.sweep_stale_runs(s, "daemon restarted")
            self._space_low = any(lib.status == "space_paused" for lib in repo.list_libraries(s))
        if swept:
            log.warning("marked %d stale runs as killed", swept)
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), random.uniform(*self.jitter_s))
        try:
            while not stop.is_set():
                try:
                    await self.tick()
                except Exception:  # noqa: BLE001
                    log.exception("scheduler tick failed")
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), self.tick_s)
        finally:
            await self.shutdown()

    async def shutdown(self) -> None:
        for process in list(self.active.values()):
            await process.kill()
        if self.tasks:
            await asyncio.gather(*self.tasks, return_exceptions=True)
