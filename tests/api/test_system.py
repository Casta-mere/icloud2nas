from __future__ import annotations

import json
import time

from fastapi.testclient import TestClient

from icloud2nas.api.state import AppState
from icloud2nas.orchestrator.store import repo
from icloud2nas.orchestrator.store.models import utcnow


def test_healthz_and_metrics_open(api: tuple[TestClient, AppState]) -> None:
    client, _ = api
    health = client.get("/healthz")
    assert health.status_code == 200
    assert health.json() == {"ok": True}
    metrics = client.get("/metrics")
    assert metrics.status_code == 200
    assert metrics.headers["content-type"] == "text/plain; version=0.0.4; charset=utf-8"
    assert "i2n_up 1" in metrics.text
    assert "i2n_workers_running 0" in metrics.text
    assert "# TYPE i2n_trash_bytes gauge" in metrics.text


def test_metrics_labels_are_escaped_and_samples_omitted(api: tuple[TestClient, AppState]) -> None:
    client, state = api
    with state.store.session() as s:
        account = repo.create_account(
            s,
            display_name='Ann "A"\\x\nz',
            apple_id="a@b.c",
            region="cn",
            password_enc=b"x",
            client_id="c1",
        )
        lib = repo.upsert_library(
            s,
            account,
            kind="personal",
            zone_name="PrimarySync",
            display_name='L"1',
            target_dir="x",
            interval_min=60,
            nightly_at="03:00",
        )
        lib.last_success_at = utcnow()
        lib.stats_json = json.dumps({"files": 7})
        repo.upsert_library(
            s,
            account,
            kind="shared",
            zone_name="SharedSync-1",
            display_name="L2",
            target_dir="y",
            interval_min=60,
            nightly_at="03:00",
        )
    text = client.get("/metrics").text
    assert 'i2n_library_files{library="L\\"1"} 7' in text
    assert 'i2n_library_last_success_timestamp{library="L\\"1"}' in text
    assert 'library="L2"' not in text
    assert 'account="Ann \\"A\\"\\\\x\\nz"' in text


def test_status_and_global_pause(api: tuple[TestClient, AppState]) -> None:
    client, _ = api
    assert client.post("/api/pause").json() == {"paused": True}
    status = client.get("/api/status").json()
    assert status["paused"] is True
    assert status["workers_running"] == 0
    assert status["space_low"] is False
    assert status["version"] and status["started_at"]
    assert client.post("/api/resume").json() == {"paused": False}
    assert client.get("/api/status").json()["paused"] is False


def test_healthz_503_when_scheduler_dead(api: tuple[TestClient, AppState]) -> None:
    client, _ = api
    client.app.state.i2n_scheduler_task.cancel()  # type: ignore[attr-defined]
    time.sleep(0.2)
    response = client.get("/healthz")
    assert response.status_code == 503
    assert response.json()["ok"] is False


def test_origin_is_remembered_but_not_from_probes(api: tuple[TestClient, AppState]) -> None:
    client, state = api
    client.get("/healthz")
    assert state.last_origin is None
    client.get("/api/status", headers={"host": "nas.local:8080"})
    assert state.last_origin == "http://nas.local:8080"
