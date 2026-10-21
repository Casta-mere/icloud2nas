"""Periodic housekeeping: trash purge, run/log retention, trash size cache."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path
from typing import Any

from icloud2nas.engine.trash import purge_expired, trash_bytes
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

    def purge_trash(self) -> list[Path]:
        today = local_now(self.clock(), self.config.tz).date()
        return purge_expired(
            self.config.photos_dir, int(self.settings.get("trash_retention_days")), today
        )

    def prune_runs(self) -> int:
        with self.store.session() as s:
            keep = int(self.settings.get("log_retention_runs"))
            paths = repo.prune_runs(s, keep)
        for p in paths:
            Path(p).unlink(missing_ok=True)
        return len(paths)

    def refresh_trash_bytes(self) -> int:
        self.trash_bytes = trash_bytes(self.config.photos_dir)
        return self.trash_bytes

    def start(self) -> None:
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
