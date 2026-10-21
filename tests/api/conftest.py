from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from icloud2nas.api.state import AppState, build_state
from icloud2nas.main import create_app
from icloud2nas.orchestrator.config import Config

PASSWORD = "hunter2"


def make_env(config: Config) -> dict[str, str]:
    return {
        "I2N_DATA_DIR": str(config.data_dir),
        "I2N_PHOTOS_DIR": str(config.photos_dir),
        "I2N_WORKER_CMD": " ".join(config.worker_cmd),
        "TZ": "Asia/Shanghai",
        "I2N_JITTER_S": "0,0",
    }


@pytest.fixture
def sent() -> list[tuple[list[str], str, str]]:
    return []


@pytest.fixture
def api(
    config: Config, sent: list[tuple[list[str], str, str]]
) -> Iterator[tuple[TestClient, AppState]]:
    def recording_sender(urls: list[str], title: str, body: str) -> bool:
        sent.append((urls, title, body))
        return True

    env = make_env(config)
    state = build_state(config, env, send=recording_sender)
    app = create_app(config, env, state=state)
    with TestClient(app) as client:
        yield client, state


def login(client: TestClient, password: str = PASSWORD) -> None:
    response = client.post("/api/session", json={"password": password})
    assert response.status_code == 204, response.text
