from __future__ import annotations

import asyncio
import json
import time

from fastapi.testclient import TestClient

from icloud2nas.api.events import SseHub
from icloud2nas.api.state import AppState
from icloud2nas.orchestrator.bus import EventBus

from .test_auth import _set_password


def _read_until(lines, prefix: str) -> str:  # type: ignore[no-untyped-def]
    for line in lines:
        if line.startswith(prefix):
            return str(line)
    raise AssertionError(f"stream ended before {prefix!r}")


def test_stream_hello_then_bus_event(api: tuple[TestClient, AppState]) -> None:
    client, state = api
    with client.stream("GET", "/api/events") as r:
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        lines = r.iter_lines()
        _read_until(lines, "event: hello")
        assert "server_time" in json.loads(_read_until(lines, "data:")[5:])
        state.bus.publish("library.updated", library_id="x")
        _read_until(lines, "event: library.updated")
        data = json.loads(_read_until(lines, "data:")[5:])
        assert data["library_id"] == "x"


def test_unregister_on_disconnect(api: tuple[TestClient, AppState]) -> None:
    client, state = api
    assert state.sse is not None
    with client.stream("GET", "/api/events") as r:
        lines = r.iter_lines()
        _read_until(lines, "event: hello")
        assert state.sse.clients() == 1
    deadline = time.monotonic() + 2
    while state.sse.clients() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert state.sse.clients() == 0


def test_requires_session(api: tuple[TestClient, AppState]) -> None:
    client, state = api
    _set_password(state)
    assert client.get("/api/events").status_code == 401


async def test_full_queue_drops_oldest() -> None:
    bus = EventBus()
    hub = SseHub(bus, max_queue=2)
    hub.start()
    q = hub.register()
    for n in range(3):
        bus.publish("run.event", n=n)
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert [q.get_nowait()[1]["n"] for _ in range(q.qsize())] == [1, 2]
    hub.stop()


async def test_heartbeat_when_silent() -> None:
    hub = SseHub(EventBus(), heartbeat_s=0.05)
    hub.start()
    frames: list[bytes] = []
    agen = hub.stream()

    async def collect() -> None:
        async for frame in agen:
            frames.append(frame)
            if frame == b": ping\n\n":
                return

    await asyncio.wait_for(collect(), 2)
    await agen.aclose()
    assert frames[0].startswith(b"event: hello\n")
    assert frames[-1] == b": ping\n\n"
    assert hub.clients() == 0
    hub.stop()
