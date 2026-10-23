"""Ops facade: every mutation the HTTP API and the CLI perform (spec §5)."""

from __future__ import annotations

import asyncio
import errno
import logging
import os
import shutil
import stat
from datetime import date
from pathlib import Path
from typing import Literal

from sqlalchemy.orm import Session

from icloud2nas.engine.trash import (
    DELETED_DIRNAME,
    OnCreate,
    open_dir,
    open_existing_dir,
    restore_library_dir,
    trash_library_dir,
)
from icloud2nas.orchestrator import owners
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


def _owner_hook(owner: tuple[int, int] | None) -> OnCreate | None:
    """An on_create that fchowns each folder we make to owner (None: leave them as made)."""
    if owner is None:
        return None
    held = owner

    def made(fd: int, shown: Path) -> None:
        owners.chown_fd(fd, shown, held)

    return made


def _rename_below(photos: Path, src: Path, dest: Path, owner: tuple[int, int] | None) -> None:
    """Rename the folder src to dest, both parents reached from the photos root one O_NOFOLLOW
    component at a time (a NAS user owning a parent cannot swap in a link); parents made for
    dest are fchowned to owner. A symlinked component, or src being a link, is a ValueError.
    """
    src_fd = open_existing_dir(photos, src.parent)
    try:
        st = os.stat(src.name, dir_fd=src_fd, follow_symlinks=False)
        if not stat.S_ISDIR(st.st_mode):
            raise ValueError(f"{str(src)!r} is a symlink or not a folder; refusing to move it")
        dest_fd = open_dir(photos, dest.parent, create=True, on_create=_owner_hook(owner))
        try:
            os.rename(src.name, dest.name, src_dir_fd=src_fd, dst_dir_fd=dest_fd)
        except OSError as exc:
            if exc.errno != errno.EXDEV:
                raise
            raise ValueError(CROSS_DEVICE) from None
        finally:
            os.close(dest_fd)
    finally:
        os.close(src_fd)


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
                account = repo.get_account(s, library.account_id)
                owner = owners.owner_for(account) if account is not None else None
                # before the row: failure keeps it
                self._move_library_dirs(old_dir, new_dir, owner)
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

    def _move_library_dirs(self, old: str, new: str, owner: tuple[int, int] | None) -> None:
        """One rename of the library folder; its .icloud2nas trash and kept travel with it.

        The rename keeps the folder's owner; parents we create are handed to the library owner.
        """
        photos = self.config.photos_dir
        src, dest = photos / old, photos / new
        if not src.exists():
            return
        self._check_destination(src, dest, new)
        _rename_below(photos, src, dest, owner)

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
            account = repo.get_account(s, account_id)
            if account is None:
                raise KeyError(account_id)
            owner = owners.owner_for(account)
            dirs = [lib.target_dir for lib in repo.libraries_of_account(s, account_id)]
        trashed = [] if keep_dir else self._trash_library_dirs(dirs, owner)
        self.scheduler.drop_manual(account_id=account_id)
        shutil.rmtree(self.config.cookie_dir(account_id).parent, ignore_errors=True)
        shutil.rmtree(self.config.account_plans_dir(account_id), ignore_errors=True)
        with self.store.session() as s:
            repo.delete_account_cascade(s, account_id)
            photos = self.config.photos_dir
            for dest in trashed:
                repo.add_deleted_dir(s, dest.relative_to(photos).as_posix())
        self.bus.publish("account.updated", account_id=account_id, deleted=True)

    def _trash_library_dirs(self, dirs: list[str], owner: tuple[int, int] | None) -> list[Path]:
        """Rename every folder into <parent>/.icloud2nas-deleted, or none of them (spec §16.1)."""
        photos = self.config.photos_dir
        today = date.today()
        present = [rel for rel in dirs if (photos / rel).exists()]
        for rel in present:  # check all before moving any
            src = photos / rel
            if os.path.ismount(src) or not _same_device(src, src.parent / DELETED_DIRNAME / "x"):
                raise ValueError(f"{rel!r}: {CROSS_DEVICE}")
        moved: list[tuple[Path, Path]] = []
        for rel in present:
            try:
                # the .icloud2nas-deleted it makes is fchowned to the owner
                dest = trash_library_dir(
                    photos / rel, today, photos=photos, on_create=_owner_hook(owner)
                )
            except (OSError, ValueError) as exc:
                for home, trashed in reversed(moved):  # put back what already moved
                    try:
                        restore_library_dir(trashed, home, photos=photos)
                    except (OSError, ValueError):
                        log.exception("could not move %s back to %s", trashed, home)
                reason = exc.strerror or str(exc) if isinstance(exc, OSError) else str(exc)
                raise ValueError(f"could not move {rel!r} to the trash: {reason}") from exc
            if dest is not None:
                moved.append((photos / rel, dest))
        return [dest for _, dest in moved]

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

