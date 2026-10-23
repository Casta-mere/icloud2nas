"""Periodic housekeeping: trash purge, run/log retention, trash size cache, legacy migration."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from icloud2nas.engine.trash import (
    migrate_legacy,
    purge_expired,
    remove_deleted_dir,
    trash_bytes,
)
from icloud2nas.orchestrator.bus import EventBus
from icloud2nas.orchestrator.config import Config
from icloud2nas.orchestrator.scheduler import local_now
from icloud2nas.orchestrator.settings import Settings
from icloud2nas.orchestrator.store import repo
from icloud2nas.orchestrator.store.db import Store
from icloud2nas.orchestrator.store.models import utcnow

log = logging.getLogger(__name__)


class Housekeeping:
    def __init__(
        self,
        config: Config,
        store: Store,
        settings: Settings,
        bus: EventBus,
        *,
        clock: Callable[[], datetime] = utcnow,
        tick_s: float = 3600.0,
    ) -> None:
        self.config = config
        self.store = store
        self.settings = settings
        self.bus = bus
        self.clock = clock
        self.tick_s = tick_s
        self.trash_bytes = 0
        self._last_purge: date | None = None
        self._unsubscribe: Callable[[], None] | None = None
        self._migrated = False

    def _library_dirs(self) -> list[Path]:
        with self.store.session() as s:
            rels = [lib.target_dir for lib in repo.list_libraries(s)]
        return [self.config.photos_dir / rel for rel in rels]

    def purge_trash(self) -> list[Path]:
        """Expired trash days of every library, then expired deleted-library folders."""
        today = local_now(self.clock(), self.config.tz).date()
        retention = int(self.settings.get("trash_retention_days"))
        removed: list[Path] = []
        for library_dir in self._library_dirs():
            try:
                removed += purge_expired(
                    library_dir, retention, today, photos=self.config.photos_dir
                )
            except ValueError as exc:  # a symlinked library or store: never purge through it
                log.warning("not purging the trash of %s: %s", library_dir, exc)
        if retention <= 0:
            return removed
        cutoff = today - timedelta(days=retention)
        with self.store.session() as s:
            rows = [(r.id, r.path, r.deleted_at) for r in repo.list_deleted_dirs(s)]
        for row_id, rel, deleted_at in rows:
            if local_now(deleted_at, self.config.tz).date() >= cutoff:
                continue
            path = self.config.photos_dir / rel
            try:
                if remove_deleted_dir(path, self.config.photos_dir):
                    removed.append(path)
            except ValueError:
                log.warning("deleted_dirs row %s has a bad path %r; dropping it", row_id, rel)
            with self.store.session() as s:
                repo.delete_deleted_dir(s, row_id)
        return removed

    def migrate_legacy_layout(self) -> list[str]:
        """Move Plan 2's /photos/.trash and /photos/kept into the libraries (once per process)."""
        if self._migrated:
            return []
        self._migrated = True
        with self.store.session() as s:
            rels = [lib.target_dir for lib in repo.list_libraries(s)]
        try:
            lines = migrate_legacy(self.config.photos_dir, rels)
        except OSError:
            log.exception("legacy trash migration failed")
            return []
        for line in lines:
            (log.warning if line.startswith("warning:") else log.info)("%s", line)
        return lines

    def prune_runs(self) -> int:
        with self.store.session() as s:
            keep = int(self.settings.get("log_retention_runs"))
            paths = repo.prune_runs(s, keep)
        for p in paths:
            Path(p).unlink(missing_ok=True)
        return len(paths)

    def refresh_trash_bytes(self) -> int:
        self.trash_bytes = trash_bytes(self._library_dirs(), photos=self.config.photos_dir)
        return self.trash_bytes

    def start(self) -> None:
        self.migrate_legacy_layout()  # before run_forever's first purge
        if self._unsubscribe is None:
            self._unsubscribe = self.bus.subscribe(self._on_event)

    def stop(self) -> None:
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None

    def _on_event(self, topic: str, _payload: dict[str, Any]) -> None:
        if topic == "run.finished":
            self.prune_runs()

    async def run_forever(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                today = local_now(self.clock(), self.config.tz).date()
                if today != self._last_purge:
                    # rmtree / rglob over the trash: off the event loop
                    await asyncio.to_thread(self.purge_trash)
                    self._last_purge = today
                self.prune_runs()  # DB rows plus a few log files
                await asyncio.to_thread(self.refresh_trash_bytes)
            except Exception:  # noqa: BLE001 - housekeeping must never stop the loop
                log.exception("housekeeping iteration failed")
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), self.tick_s)
