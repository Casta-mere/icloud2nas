"""AppState: every long-lived object the web process owns, wired once."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from icloud2nas.orchestrator.bus import EventBus
from icloud2nas.orchestrator.config import Config
from icloud2nas.orchestrator.housekeeping import Housekeeping
from icloud2nas.orchestrator.notify import Notifier, Sender
from icloud2nas.orchestrator.onboarding import Onboarding
from icloud2nas.orchestrator.ops import Ops
from icloud2nas.orchestrator.runs import RunManager
from icloud2nas.orchestrator.scheduler import Scheduler
from icloud2nas.orchestrator.settings import Settings
from icloud2nas.orchestrator.store.db import Store, make_engine, upgrade_db
from icloud2nas.orchestrator.store.models import utcnow
from icloud2nas.orchestrator.store.secrets import Secrets


@dataclass
class AppState:
    config: Config
    store: Store
    settings: Settings
    secrets: Secrets
    bus: EventBus
    runs: RunManager
    onboarding: Onboarding
    scheduler: Scheduler
    ops: Ops
    notifier: Notifier
    housekeeping: Housekeeping
    sse: Any | None  # SseHub, created with the events router
    started_at: datetime
    last_origin: str | None


def build_state(
    config: Config, env: Mapping[str, str] | None = None, *, send: Sender | None = None
) -> AppState:
    source = os.environ if env is None else env
    config.ensure_layout()
    engine = make_engine(config.db_path)
    upgrade_db(engine)
    store = Store(engine)
    settings = Settings(store)
    settings.seed_from_env(
        {
            "I2N_MAX_WORKERS": str(config.max_workers),
            "I2N_MIN_FREE_GB": str(config.min_free_gb),
        }
    )
    secrets = Secrets.from_config(config, source)
    bus = EventBus()
    runs = RunManager(config, store, secrets, bus)
    onboarding = Onboarding(config, store, secrets, bus, runs, settings)
    lo, _, hi = source.get("I2N_JITTER_S", "0,300").partition(",")
    scheduler = Scheduler(
        config, store, secrets, bus, runs, onboarding, settings, jitter_s=(int(lo), int(hi or lo))
    )
    ops = Ops(config, store, settings, bus, scheduler, onboarding)
    housekeeping = Housekeeping(config, store, settings, bus)

    def last_origin() -> str | None:
        return state.last_origin

    notifier = (
        Notifier(store, settings, bus, send=send, link_fallback=last_origin)
        if send is not None
        else Notifier(store, settings, bus, link_fallback=last_origin)
    )
    state = AppState(
        config,
        store,
        settings,
        secrets,
        bus,
        runs,
        onboarding,
        scheduler,
        ops,
        notifier,
        housekeeping,
        None,
        utcnow(),
        None,
    )
    return state
