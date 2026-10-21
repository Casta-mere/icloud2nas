"""Ops facade: every mutation the HTTP API and the CLI perform (spec §5)."""

from __future__ import annotations

import asyncio
import errno
import logging
import os
import shutil
from datetime import date
from pathlib import Path
from typing import Literal

from sqlalchemy.orm import Session

from icloud2nas.engine.trash import TRASH_DIRNAME, trash_day_dir, trash_directory
from icloud2nas.orchestrator.bus import EventBus
from icloud2nas.orchestrator.config import Config, normalize_hhmm
from icloud2nas.orchestrator.onboarding import Onboarding
from icloud2nas.orchestrator.scheduler import DueJob, Scheduler
from icloud2nas.orchestrator.settings import Settings
from icloud2nas.orchestrator.store import repo
from icloud2nas.orchestrator.store.db import Store
from icloud2nas.orchestrator.store.models import ReconcilePlan

log = logging.getLogger(__name__)

CROSS_DEVICE = "cross-device: move the folder manually, then set the directory"


class Busy(RuntimeError):
    """The target is mid-run or mid-auth (the API answers 409)."""


def _nested(a: str, b: str) -> bool:
    """True when one library rel is the other or lies inside it."""
    pa, pb = Path(a).parts, Path(b).parts
    shorter = min(len(pa), len(pb))
    return pa[:shorter] == pb[:shorter]


def _same_device(src: Path, dest: Path) -> bool:
    """Whether a rename of src to dest stays on one filesystem (dest need not exist yet)."""
    ancestor = dest.parent
    while not ancestor.exists():
        ancestor = ancestor.parent
    return os.stat(src).st_dev == os.stat(ancestor).st_dev


def _rename(src: Path, dest: Path) -> None:
    try:
        os.rename(src, dest)
    except OSError as exc:
        if exc.errno != errno.EXDEV:
            raise
        raise ValueError(CROSS_DEVICE) from None


class Ops:
    def __init__(
        self,
        config: Config,
        store: Store,
        settings: Settings,
        bus: EventBus,
        scheduler: Scheduler,
        onboarding: Onboarding,
    ) -> None:
        self.config = config
        self.store = store
        self.settings = settings
        self.bus = bus
        self.scheduler = scheduler
        self.onboarding = onboarding
        self._tasks: set[asyncio.Task[None]] = set()
        self._discovering: set[str] = set()  # accounts whose library discovery is running

    # -- libraries -----------------------------------------------------------------------------

    def set_library(
        self,
        library_id: str,
        *,
        display_name: str | None = None,
        target_dir: str | None = None,
        enabled: bool | None = None,
        interval_min: int | None = None,
        nightly_at: str | None = None,
    ) -> None:
        if interval_min is not None and not 5 <= interval_min <= 1440:
            raise ValueError("interval_min must be between 5 and 1440")
        nightly = normalize_hhmm(nightly_at) if nightly_at is not None else None
        with self.store.session() as s:
            library = repo.get_library(s, library_id)
            if library is None:
                raise KeyError(library_id)
            old_dir = library.target_dir
            new_dir = old_dir
            if target_dir is not None:
                new_dir = repo.validate_target_dir(
                    s, target_dir, exclude_library_id=library_id, kind=library.kind
                )
            if new_dir != old_dir:
                if _nested(old_dir, new_dir):
                    raise ValueError(
                        f"{new_dir!r} and {old_dir!r} contain one another; pick a separate folder"
                    )
                if library_id in self.scheduler.running_libraries:
                    raise Busy("library has a running job")
                self._move_library_dirs(old_dir, new_dir)  # before the row: failure keeps it
            if display_name is not None:
                library.display_name = display_name.strip()
            library.target_dir = new_dir
            if enabled is not None:
                library.enabled = enabled
            if interval_min is not None:
                library.interval_min = interval_min
            if nightly is not None:
                library.nightly_at = nightly
        self.bus.publish("library.updated", library_id=library_id)

    def _move_library_dirs(self, old: str, new: str) -> None:
        photos = self.config.photos_dir
        src, dest = photos / old, photos / new
        trash_src, trash_dest = photos / TRASH_DIRNAME / old, photos / TRASH_DIRNAME / new
        move_lib, move_trash = src.exists(), trash_src.exists()
        if move_lib:  # pre-check both moves so neither can fail half-way
            self._check_destination(src, dest, new)
        if move_trash:
            self._check_destination(trash_src, trash_dest, f"{TRASH_DIRNAME}/{new}")
        if move_lib:
            dest.parent.mkdir(parents=True, exist_ok=True)
            _rename(src, dest)
        if not move_trash:
            return
        try:
            trash_dest.parent.mkdir(parents=True, exist_ok=True)
            _rename(trash_src, trash_dest)
        except (OSError, ValueError) as exc:
            if move_lib:
                os.rename(dest, src)  # backstop: never leave the library folder orphaned
            raise ValueError(f"could not move the trash folder: {exc}") from exc

    @staticmethod
    def _check_destination(src: Path, dest: Path, label: str) -> None:
        if dest.exists() and not dest.is_dir():
            raise ValueError(f"{label!r} already exists and is not a directory")
        if dest.is_dir() and any(dest.iterdir()):
            raise ValueError(f"{label!r} already exists and is not empty")
        if not _same_device(src, dest):
            raise ValueError(CROSS_DEVICE)

    def pause_library(self, library_id: str) -> None:
        with self.store.session() as s:
            library = repo.get_library(s, library_id)
            if library is None:
                raise KeyError(library_id)
            if library.status != "ok":
                raise ValueError(f"library is {library.status}; only an ok library can be paused")
            library.status = "paused"
        self.bus.publish("library.updated", library_id=library_id)

    def resume_library(self, library_id: str) -> None:
        with self.store.session() as s:
            library = repo.get_library(s, library_id)
            if library is None:
                raise KeyError(library_id)
            if library.status == "ok":
                return
            if library.status != "paused":
                hint = "; frees up space to resume" if library.status == "space_paused" else ""
                raise ValueError(f"library is {library.status}{hint}")
            library.status = "ok"
        self.bus.publish("library.updated", library_id=library_id)

    def pause_all(self) -> None:
        self.settings.set("paused", "1")
        self.bus.publish("paused", paused=True)

    def resume_all(self) -> None:
        self.settings.set("paused", "0")
        self.bus.publish("paused", paused=False)

    def request_run(self, library_id: str, mode: Literal["sync", "reconcile"]) -> None:
        if mode not in ("sync", "reconcile"):
            raise ValueError("mode must be 'sync' or 'reconcile'")
        with self.store.session() as s:
            library = repo.get_library(s, library_id)
            if library is None:
                raise KeyError(library_id)
            account_id = library.account_id
        kind = "full" if mode == "sync" else "reconcile"
        self.scheduler.enqueue(DueJob(library_id, account_id, kind, "manual"))

    # -- reconcile plans -----------------------------------------------------------------------

    def approve_plan(self, plan_id: str) -> None:
        with self.store.session() as s:
            plan = s.get(ReconcilePlan, plan_id)
            if plan is None:
                raise KeyError(plan_id)
            if plan.status != "pending":
                raise ValueError(f"plan is {plan.status}, not pending")
            plan.status = "approved"
            library_id = plan.library_id
            account_id = self._account_of(s, library_id)
        try:
            self.scheduler.enqueue(DueJob(library_id, account_id, "apply_plan", "manual", plan_id))
        except Exception:
            with self.store.session() as s:
                plan = s.get(ReconcilePlan, plan_id)
                if plan is not None and plan.status == "approved":
                    plan.status = "pending"
            raise

    @staticmethod
    def _account_of(s: Session, library_id: str) -> str:
        library = repo.get_library(s, library_id)
        if library is None:
            raise KeyError(library_id)
        return library.account_id

    def reject_plan(self, plan_id: str) -> None:
        with self.store.session() as s:
            plan = s.get(ReconcilePlan, plan_id)
            if plan is None:
                raise KeyError(plan_id)
            if plan.status != "pending":
                raise ValueError(f"plan is {plan.status}, not pending")
            plan.status = "rejected"
            library_id = plan.library_id
        self.bus.publish(
            "plan.resolved", plan_id=plan_id, library_id=library_id, status="rejected"
        )

    # -- accounts ------------------------------------------------------------------------------

    def check_idle(self, account_id: str) -> None:
        if self.scheduler.account_busy(account_id):
            raise Busy("account has an active run or auth session")

    def _claim(self, account_id: str, owner: str) -> None:
        """Check idle and hold the account in one synchronous step (no await in between)."""
        self.check_idle(account_id)
        if not self.scheduler.hold_account(account_id, owner):
            raise Busy("account has an active run or auth session")

    def delete_account(self, account_id: str, *, keep_dir: bool) -> None:
        self.check_idle(account_id)
        with self.store.session() as s:
            if repo.get_account(s, account_id) is None:
                raise KeyError(account_id)
            dirs = [lib.target_dir for lib in repo.libraries_of_account(s, account_id)]
        if not keep_dir:
            self._trash_library_dirs(dirs)
        self.scheduler.drop_manual(account_id=account_id)
        shutil.rmtree(self.config.cookie_dir(account_id).parent, ignore_errors=True)
        with self.store.session() as s:
            repo.delete_account_cascade(s, account_id)
        self.bus.publish("account.updated", account_id=account_id, deleted=True)

    def _trash_library_dirs(self, dirs: list[str]) -> None:
        """Move every folder to the trash by rename, or none of them: no copying, no half state."""
        photos = self.config.photos_dir
        today = date.today()
        present = [rel for rel in dirs if (photos / rel).exists()]
        for rel in present:  # check all before moving any
            src = photos / rel
            if os.path.ismount(src) or not _same_device(src, trash_day_dir(photos, rel, today)):
                raise ValueError(f"{rel!r}: {CROSS_DEVICE}")
        moved: list[tuple[Path, Path]] = []
        for rel in present:
            try:
                dest = trash_directory(photos, rel, today, allow_copy=False)
            except OSError as exc:
                for home, trashed in reversed(moved):  # put back what already moved
                    try:
                        os.rename(trashed, home)
                    except OSError:
                        log.exception("could not move %s back to %s", trashed, home)
                reason = CROSS_DEVICE if exc.errno == errno.EXDEV else exc.strerror or str(exc)
                raise ValueError(f"could not move {rel!r} to the trash: {reason}") from exc
            if dest is not None:
                moved.append((photos / rel, dest))

    def _release(self, account_id: str, owner: str) -> None:
        self._discovering.discard(account_id)
        self.scheduler.release_account(account_id, owner)

    async def start_auth(self, account_id: str, *, discover: bool = True) -> str:
        auth_owner = f"auth:{account_id}"
        self._claim(account_id, auth_owner)  # before the first await: no window for a 2nd claim
        try:
            session = await self.onboarding.start_auth(account_id, trigger="reauth")
        finally:
            # from here on onboarding.sessions keeps the account busy
            self.scheduler.release_account(account_id, auth_owner)
        if discover:
            owner = f"discovery:{account_id}"
            self.scheduler.hold_account(account_id, owner)  # busy through discovery, no gap
            self._discovering.add(account_id)
            task = asyncio.create_task(self._discover_after_auth(account_id, owner))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
        return session.run_id

    @property
    def discovering(self) -> bool:
        return bool(self._discovering)

    async def refresh_libraries(self, account_id: str) -> tuple[list[str], list[str]]:
        """Manual library discovery; the account is busy while the list worker runs."""
        owner = f"refresh:{account_id}"
        self._claim(account_id, owner)
        self._discovering.add(account_id)
        try:
            return await self.onboarding.refresh_libraries(account_id)
        finally:
            self._release(account_id, owner)

    async def shutdown(self) -> None:
        """Cancel background library discovery (process exit)."""
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _discover_after_auth(self, account_id: str, owner: str) -> None:
        """List the account's libraries once its auth session ended with exit 0."""
        try:
            result = await self.onboarding.wait_auth(account_id)
            if result.exit_code != 0:
                return
            with self.store.session() as s:
                before = {lib.id for lib in repo.list_libraries(s)}
            await self.onboarding.refresh_libraries(account_id)
            with self.store.session() as s:
                created = [lib.id for lib in repo.list_libraries(s) if lib.id not in before]
            for library_id in created:
                self.bus.publish("library.updated", library_id=library_id)
            self.bus.publish("account.updated", account_id=account_id)
        except Exception:  # noqa: BLE001 - background chain; the account stays usable
            log.exception("library discovery after auth failed for %s", account_id)
        finally:
            self._release(account_id, owner)

    async def submit_code(self, account_id: str, code: str) -> None:
        await self.onboarding.submit_code(account_id, code)

    async def set_password(self, account_id: str, password: str) -> str:
        self.check_idle(account_id)  # before the stored password changes
        self.onboarding.set_password(account_id, password)
        return await self.start_auth(account_id)

