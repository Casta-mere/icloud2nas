"""Scheduler (spec §7): decide what is due, launch workers under locks, chain nightly."""

from __future__ import annotations

import asyncio
import collections
import json
import logging
import random
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from icloud2nas.orchestrator import owners
from icloud2nas.orchestrator.bus import EventBus
from icloud2nas.orchestrator.config import Config
from icloud2nas.orchestrator.jobs import build_job, validate_job
from icloud2nas.orchestrator.onboarding import Onboarding
from icloud2nas.orchestrator.runs import RunManager
from icloud2nas.orchestrator.settings import Settings
from icloud2nas.orchestrator.store import repo
from icloud2nas.orchestrator.store.db import Store
from icloud2nas.orchestrator.store.models import Library, ReconcilePlan, utcnow
from icloud2nas.orchestrator.store.secrets import Secrets
from icloud2nas.orchestrator.supervisor import WorkerProcess, WorkerResult

log = logging.getLogger(__name__)
RUN_TIMEOUT_S = 12 * 3600
KIND_ORDER = {"apply_plan": 0, "nightly": 0, "reconcile": 1, "full": 2, "quick": 3}


@dataclass(frozen=True)
class DueJob:
    library_id: str
    account_id: str
    kind: str  # quick | full | nightly | reconcile | apply_plan
    trigger: str  # schedule | nightly | manual
    plan_id: str | None = None


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
        # account_id -> owner token (a run id, "auth:<id>", "discovery:<id>", ...): one holder
        # per account, and only the holder can release it
        self.running_accounts: dict[str, str] = {}
        self.active: dict[str, WorkerProcess] = {}
        self.tasks: set[asyncio.Task[None]] = set()
        self._space_low = False
        self.manual: collections.deque[DueJob] = collections.deque()
        self._wake = asyncio.Event()  # enqueue() sets it: a manual request launches now

    @property
    def space_low(self) -> bool:
        return self._space_low

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

    def _folder_exists(self, library: Library) -> bool:
        return (self.config.photos_dir / library.target_dir).is_dir()

    @staticmethod
    def _transition_dir_state(library: Library, exists: bool) -> bool:
        """Move a synced library into or out of dir_missing; True when its status changed.

        space_paused is never touched (space state wins; the folder is checked again once space
        recovers). A library that never finished a full sync is unaffected.
        """
        if library.first_full_at is None:
            return False
        stats = library.stats
        if not exists and library.status in ("ok", "paused"):
            stats["status_before_missing"] = library.status
            library.stats_json = json.dumps(stats, ensure_ascii=False)
            library.status = "dir_missing"
            return True
        if exists and library.status == "dir_missing":
            before = stats.pop("status_before_missing", "ok")
            library.stats_json = json.dumps(stats, ensure_ascii=False)
            library.status = before if before in ("ok", "paused") else "ok"
            return True
        return False

    def _apply_dir_state(self, library_id: str | None = None) -> bool:
        """Sync dir_missing with the disk (all libraries, or one); True when it is blocked."""
        changed: list[str] = []
        blocked = False
        with self.store.session() as s:
            libraries = (
                repo.list_libraries(s)
                if library_id is None
                else [lib for lib in [repo.get_library(s, library_id)] if lib is not None]
            )
            for library in libraries:
                if library.first_full_at is None:
                    continue
                if self._transition_dir_state(library, self._folder_exists(library)):
                    changed.append(library.id)
                blocked = blocked or library.status == "dir_missing"
        for changed_id in changed:
            self.bus.publish("library.updated", library_id=changed_id)
        return blocked

    def due_jobs(self, now: datetime | None = None) -> list[DueJob]:
        now = now or self.clock()
        if self.paused() or not self._apply_space_state():
            self._apply_dir_state()
            return []
        self._apply_dir_state()  # after space recovery, so a recovered library is checked too
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

    def enqueue(self, job: DueJob) -> None:
        """Queue an explicit request; it ignores pause (an explicit request wins)."""
        with self.store.session() as s:
            library = repo.get_library(s, job.library_id)
            if library is None:
                raise KeyError(job.library_id)
            account = repo.get_account(s, library.account_id)
            if not library.enabled:
                raise ValueError("library is disabled")
            if account is None or account.status != "ok":
                raise ValueError("account is not ok")
        if self._apply_dir_state(job.library_id):
            from icloud2nas.orchestrator.ops import Busy  # ops imports this module

            raise Busy("library folder is missing")
        if not self.free_space_ok():
            raise RuntimeError("space low")
        key = (job.library_id, job.kind, job.plan_id)
        if any((j.library_id, j.kind, j.plan_id) == key for j in self.manual):
            return
        self.manual.append(job)
        self._wake.set()

    def pending_manual(self) -> list[DueJob]:
        return list(self.manual)

    def drop_manual(self, *, account_id: str) -> int:
        """Forget queued manual jobs of an account (deleted, or no longer able to run)."""
        dropped = [job for job in self.manual if job.account_id == account_id]
        for job in dropped:
            self.manual.remove(job)
            self._abandon(job, "its account was removed")
        return len(dropped)

    def _abandon(self, job: DueJob, reason: str) -> None:
        log.warning(
            "dropping queued %s job for library %s: %s", job.kind, job.library_id, reason
        )
        if job.kind != "apply_plan" or job.plan_id is None:
            return
        with self.store.session() as s:  # an approved plan that will not run goes back
            plan = s.get(ReconcilePlan, job.plan_id)
            if plan is None or plan.status != "approved":
                return
            plan.status = "pending"
        self.bus.publish(
            "plan.resolved", plan_id=job.plan_id, library_id=job.library_id, status="pending"
        )

    def _still_valid(self, job: DueJob) -> str | None:
        """Why a queued manual job can no longer run (None when it still can)."""
        with self.store.session() as s:
            library = repo.get_library(s, job.library_id)
            if library is None or library.account_id != job.account_id:
                return "its library is gone"
            if not library.enabled:
                return "its library is disabled"
            account = repo.get_account(s, job.account_id)
            if account is None:
                return "its account is gone"
            if job.kind != "apply_plan" and account.status != "ok":
                return f"its account is {account.status}"  # apply_plan never authenticates
        if self._apply_dir_state(job.library_id):
            return "its library folder is missing"
        return None

    async def wait_idle(self, library_id: str) -> None:
        while library_id in self.running_libraries:
            await asyncio.sleep(0.01)

    # -- launching -----------------------------------------------------------------------------

    def account_busy(self, account_id: str) -> bool:
        return account_id in self.running_accounts or account_id in self.onboarding.sessions

    def hold_account(self, account_id: str, owner: str) -> bool:
        """Mark the account busy for `owner`; False when someone else already holds it."""
        holder = self.running_accounts.get(account_id)
        if holder is not None and holder != owner:
            return False
        self.running_accounts[account_id] = owner
        return True

    def release_account(self, account_id: str, owner: str) -> None:
        """Drop `owner`'s hold; a no-op when the account is held by someone else (or nobody)."""
        if self.running_accounts.get(account_id) == owner:
            del self.running_accounts[account_id]

    def can_launch(self, job: DueJob) -> bool:
        return (
            job.library_id not in self.running_libraries
            and not self.account_busy(job.account_id)
            and len(self.running_libraries) < self.settings.get("max_workers")
            and not self._apply_dir_state(job.library_id)
        )

    async def launch(self, job: DueJob) -> str:
        """Start the job; returns its run id ("" for apply_plan: its run starts once the plan
        is confirmed still approved)."""
        if not self.can_launch(job):
            raise RuntimeError("busy")
        # no await from the check to the holds: nothing else can claim the account meanwhile
        run_id = (
            ""
            if job.kind == "apply_plan"
            else self.runs.start_run(
                account_id=job.account_id,
                library_id=job.library_id,
                mode="reconcile" if job.kind == "reconcile" else "sync",
                trigger=job.trigger,
            )
        )
        owner = run_id or f"apply_plan:{job.plan_id}"
        self.running_libraries.add(job.library_id)
        self.running_accounts[job.account_id] = owner
        task = asyncio.create_task(self._run_chain(job, run_id, owner))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return run_id

    async def _run_one(
        self,
        job: DueJob,
        run_id: str,
        mode: str,
        full: bool,
        *,
        apply_plan: str | None = None,
    ) -> int:
        if self._dir_vanished(job.library_id):
            # decided before prepare_spawn, which would create the folder and re-download into it
            return self._fail_dir_missing(run_id)
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
                password = None if apply_plan else self.onboarding.password_for(account)
                owner = owners.owner_for(account)
                library_dir = self.config.photos_dir / library.target_dir
                synced = library.first_full_at is not None
            validate_job(mode, spec)
            owners.prepare_spawn(
                self.config, job.account_id, owner, library_dir, require_library=synced
            )
        except owners.LibraryMissing:  # vanished after the check above: same outcome
            self._dir_vanished(job.library_id)
            return self._fail_dir_missing(run_id)
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
                apply_plan=apply_plan,
                timeout_s=RUN_TIMEOUT_S,
                on_start=register,
                owner=owner,
            )
        finally:
            self.active.pop(run_id, None)
        return result.exit_code

    def _fail_dir_missing(self, run_id: str) -> int:
        self.runs.finish_run(
            run_id,
            WorkerResult(
                exit_code=1,
                events=[],
                terminal={
                    "ev": "error",
                    "kind": "dir_missing",
                    "message": "library folder is missing; not re-downloading into a new one",
                },
            ),
            account_state=False,
        )
        return 1

    def _dir_vanished(self, library_id: str) -> bool:
        """True when a synced library's folder is gone (status updated, event published)."""
        with self.store.session() as s:
            library = repo.get_library(s, library_id)
            if library is None or library.first_full_at is None:
                return False
            missing = not self._folder_exists(library)
            changed = self._transition_dir_state(library, not missing)
        if changed:
            self.bus.publish("library.updated", library_id=library_id)
        return missing

    async def _run_chain(self, job: DueJob, run_id: str, owner: str) -> None:
        started_on = local_now(self.clock(), self.config.tz).date()  # the night this run belongs to
        try:
            if job.kind == "reconcile":
                await self._run_one(job, run_id, "reconcile", full=False)
                return
            if job.kind == "apply_plan":
                await self._run_apply_plan(job)
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
            self.release_account(job.account_id, owner)

    async def _run_apply_plan(self, job: DueJob) -> None:
        assert job.plan_id is not None
        with self.store.session() as s:
            plan = s.get(ReconcilePlan, job.plan_id)
            if plan is None:
                raise KeyError(job.plan_id)
            status, plan_file = plan.status, plan.plan_file
        if status != "approved":
            log.warning("plan %s is %s, not approved: not applying", job.plan_id, status)
            self.bus.publish(
                "plan.resolved", plan_id=job.plan_id, library_id=job.library_id, status=status
            )
            return
        run_id = self.runs.start_run(
            account_id=job.account_id,
            library_id=job.library_id,
            mode="reconcile",
            trigger="manual",
        )
        code = 1
        try:
            code = await self._run_one(job, run_id, "reconcile", full=False, apply_plan=plan_file)
        finally:
            final = "executed" if code == 0 else "pending"
            with self.store.session() as s:
                plan = s.get(ReconcilePlan, job.plan_id)
                if plan is not None:
                    plan.status = final
            self.bus.publish(
                "plan.resolved", plan_id=job.plan_id, library_id=job.library_id, status=final
            )

    async def launch_manual(self) -> int:
        """Launch the queued manual jobs that can start now; drop those that never can."""
        launched = 0
        for job in self.pending_manual():
            reason = self._still_valid(job)
            if reason is not None:
                self.manual.remove(job)
                self._abandon(job, reason)
                continue
            if not self.can_launch(job):
                continue
            self.manual.remove(job)
            try:
                await self.launch(job)
            except Exception:  # noqa: BLE001 - one bad job must not starve the rest
                log.exception("manual %s job for library %s failed to launch; dropped",
                              job.kind, job.library_id)
                self._abandon(job, "launch failed")
                continue
            launched += 1
        return launched

    async def tick(self) -> int:
        launched = await self.launch_manual()
        for job in self.due_jobs():
            if not self.can_launch(job):
                continue
            try:
                await self.launch(job)
            except Exception:  # noqa: BLE001 - retried on a later tick
                log.exception("%s job for library %s failed to launch", job.kind, job.library_id)
                continue
            launched += 1
        return launched

    # -- daemon --------------------------------------------------------------------------------

    async def run_forever(self, stop: asyncio.Event) -> None:
        with self.store.session() as s:
            swept = repo.sweep_stale_runs(s, "daemon restarted")
            plans = repo.sweep_approved_plans(s)
            self._space_low = any(lib.status == "space_paused" for lib in repo.list_libraries(s))
        if swept:
            log.warning("marked %d stale runs as killed", swept)
        if plans:
            log.warning("reset %d approved plans to pending", plans)
        await self._startup_jitter(stop)
        try:
            while not stop.is_set():
                try:
                    await self.tick()
                except Exception:  # noqa: BLE001
                    log.exception("scheduler tick failed")
                await self._sleep(stop, self.tick_s)
        finally:
            await self.shutdown()

    async def _startup_jitter(self, stop: asyncio.Event) -> None:
        """Hold due jobs back for a random delay after start, but serve manual requests at once."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + random.uniform(*self.jitter_s)
        while not stop.is_set():
            if self.manual:
                try:
                    await self.launch_manual()
                except Exception:  # noqa: BLE001
                    log.exception("manual launch during startup jitter failed")
            remaining = deadline - loop.time()
            if remaining <= 0:
                return
            await self._sleep(stop, remaining)

    async def _sleep(self, stop: asyncio.Event, timeout: float) -> None:
        """Wait up to timeout, returning early on stop or when a manual request is queued."""
        waiters = {asyncio.create_task(stop.wait()), asyncio.create_task(self._wake.wait())}
        try:
            await asyncio.wait(waiters, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for waiter in waiters:
                waiter.cancel()
            await asyncio.gather(*waiters, return_exceptions=True)
            self._wake.clear()

    async def shutdown(self) -> None:
        """Kill every worker at once (runs and interactive logins), then let the chains settle.

        Concurrent kills keep the whole shutdown within one KILL_GRACE_S, whatever the count.
        """
        processes = [*self.active.values()]
        processes += [session.process for session in self.onboarding.sessions.values()]
        await asyncio.gather(*(p.kill() for p in processes), return_exceptions=True)
        pending: list[asyncio.Task[None]] = [*self.tasks]
        pending += [
            session.task
            for session in self.onboarding.sessions.values()
            if session.task is not None
        ]
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
