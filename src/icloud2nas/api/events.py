"""Server-sent events: fan the in-process bus out to browser clients."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from icloud2nas.api.auth import require_session
from icloud2nas.api.state import AppState
from icloud2nas.orchestrator.bus import EventBus

log = logging.getLogger(__name__)

Item = tuple[str, dict[str, Any]] | None  # None = stop sentinel


def _frame(topic: str, payload: dict[str, Any]) -> bytes:
    data = json.dumps(payload, ensure_ascii=False, default=str)
    return f"event: {topic}\ndata: {data}\n\n".encode()


class SseHub:
    def __init__(self, bus: EventBus, *, max_queue: int = 1000, heartbeat_s: float = 15.0) -> None:
        self._bus = bus
        self._max_queue = max_queue
        self._heartbeat_s = heartbeat_s
        self._clients: set[asyncio.Queue[Item]] = set()
        self._warned: set[int] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._unsubscribe: Any = None

    def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._unsubscribe = self._bus.subscribe(self.on_bus)

    def stop(self) -> None:
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None
        for queue in list(self._clients):
            self._offer(queue, None)
        self._clients.clear()
        self._warned.clear()

    def register(self) -> asyncio.Queue[Item]:
        queue: asyncio.Queue[Item] = asyncio.Queue(maxsize=self._max_queue)
        self._clients.add(queue)
        return queue

    def unregister(self, queue: asyncio.Queue[Item]) -> None:
        self._clients.discard(queue)
        self._warned.discard(id(queue))

    def clients(self) -> int:
        return len(self._clients)

    def on_bus(self, topic: str, payload: dict[str, Any]) -> None:
        """Bus callback; may run on any thread, so queues are only touched via the loop."""
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        for queue in list(self._clients):
            loop.call_soon_threadsafe(self._offer, queue, (topic, payload))

    def _offer(self, queue: asyncio.Queue[Item], item: Item) -> None:
        if queue.full():
            queue.get_nowait()
            if id(queue) not in self._warned:
                self._warned.add(id(queue))
                log.warning("sse client queue full; dropping oldest events")
        queue.put_nowait(item)

    async def stream(self, request: Request | None = None) -> AsyncIterator[bytes]:
        queue = self.register()
        try:
            hello = {"server_time": datetime.now(UTC).isoformat()}
            yield _frame("hello", hello)
            while True:
                try:
                    item = await asyncio.wait_for(queue.get(), self._heartbeat_s)
                except TimeoutError:
                    yield b": ping\n\n"
                    continue
                if item is None:
                    return
                yield _frame(*item)
        finally:
            self.unregister(queue)


router = APIRouter(prefix="/api", dependencies=[Depends(require_session)])


@router.get("/events", response_model=None)
async def events(request: Request) -> StreamingResponse:
    state: AppState = request.app.state.i2n
    assert state.sse is not None
    return StreamingResponse(
        state.sse.stream(request),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
