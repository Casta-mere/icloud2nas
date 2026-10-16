"""In-process event bus: the core publishes, Plan 2b's SSE and notifications subscribe."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

Subscriber = Callable[[str, dict[str, Any]], None]
log = logging.getLogger(__name__)


class EventBus:
    def __init__(self) -> None:
        self._subscribers: list[Subscriber] = []

    def subscribe(self, fn: Subscriber) -> Callable[[], None]:
        self._subscribers.append(fn)

        def unsubscribe() -> None:
            if fn in self._subscribers:
                self._subscribers.remove(fn)

        return unsubscribe

    def publish(self, topic: str, **payload: Any) -> None:
        for fn in list(self._subscribers):
            try:
                fn(topic, dict(payload))
            except Exception:  # noqa: BLE001 - one bad subscriber must not stop the others
                log.exception("event subscriber failed on %s", topic)
