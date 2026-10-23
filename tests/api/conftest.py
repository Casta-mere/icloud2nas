from __future__ import annotations

import json
import time
from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient

from icloud2nas.api.state import AppState
from tests.conftest import make_env as make_env  # the api fixture lives in tests/conftest.py

PASSWORD = "hunter2"


def login(client: TestClient, password: str = PASSWORD) -> None:
    response = client.post("/api/session", json={"password": password})
    assert response.status_code == 204, response.text


def wait_for(
    client: TestClient, predicate: Callable[[TestClient], Any], *, tries: int = 100
) -> Any:
    """Poll until predicate(client) is truthy; workers finish asynchronously to requests."""
    for _ in range(tries):
        value = predicate(client)
        if value:
            return value
        time.sleep(0.05)
    raise AssertionError("condition not reached")


def set_scenario(monkeypatch: pytest.MonkeyPatch, scenario: dict[str, Any]) -> None:
    monkeypatch.setenv("FAKE_WORKER_SCENARIO", json.dumps(scenario))


def onboard(
    client: TestClient, name: str = "小明", apple_id: str = "a@icloud.com", **extra: Any
) -> tuple[str, str]:
    """Create an account against the default fake worker; returns (account_id, library_id)."""
    response = client.post(
        "/api/accounts",
        json={
            "display_name": name, "apple_id": apple_id, "region": "cn", "password": "pw", **extra,
        },
    )
    assert response.status_code == 201, response.text
    account_id: str = response.json()["id"]
    library = wait_for(
        client,
        lambda c: next(
            (lib for lib in c.get("/api/libraries").json() if lib["account_id"] == account_id),
            None,
        ),
    )
    return account_id, str(library["id"])


def run_manual(client: TestClient, state: AppState, run_modes: set[str]) -> None:
    """Run one scheduler tick (it drains the manual queue) and wait for its workers."""
    client.portal.call(state.scheduler.tick)  # type: ignore[union-attr]
    wait_for(
        client,
        lambda c: not state.scheduler.running_libraries
        and {r["mode"] for r in c.get("/api/runs").json()} >= run_modes,
    )
