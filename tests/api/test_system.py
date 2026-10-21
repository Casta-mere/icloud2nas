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
        s.flush()
        lib_id = lib.id
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
    assert f'i2n_library_files{{library_id="{lib_id}",library="L\\"1"}} 7' in text
    assert f'i2n_library_last_success_timestamp{{library_id="{lib_id}",library="L\\"1"}}' in text
    assert 'library="L2"' not in text
    assert 'account="Ann \\"A\\"\\\\x\\nz"' in text


def test_status_and_global_pause(api: tuple[TestClient, AppState]) -> None:
    client, _ = api
    assert client.post("/api/pause", json={}).json() == {"paused": True}
    status = client.get("/api/status").json()
    assert status["paused"] is True
    assert status["workers_running"] == 0
    assert status["space_low"] is False
    assert status["version"] and status["started_at"]
    assert client.post("/api/resume", json={}).json() == {"paused": False}
    assert client.get("/api/status").json()["paused"] is False


def test_healthz_503_when_scheduler_dead(api: tuple[TestClient, AppState]) -> None:
    client, _ = api
    client.app.state.i2n_scheduler_task.cancel()  # type: ignore[attr-defined]
    time.sleep(0.2)
    response = client.get("/healthz")
    assert response.status_code == 503
    assert response.json()["ok"] is False


def test_origin_is_remembered_only_from_browser_page_loads(
    api: tuple[TestClient, AppState],
) -> None:
    client, state = api
    page = {"host": "nas.lan:8080", "sec-fetch-mode": "navigate", "accept": "text/html"}
    client.get("/healthz", headers=page)
    assert state.last_origin is None
    # the CLI / API clients: JSON, no navigation
    client.get("/api/status", headers={"host": "nas.lan:8080", "accept": "application/json"})
    client.post("/api/pause", headers={"host": "nas.lan:8080"}, json={})
    assert state.last_origin is None
    for loopback in ("127.0.0.1:8080", "localhost", "[::1]:8080", "host.docker.internal:8080"):
        client.get("/", headers={**page, "host": loopback})
    assert state.last_origin is None
    client.get("/", headers={"host": "nas.lan:8080", "accept": "text/html,application/xhtml+xml"})
    assert state.last_origin == "http://nas.lan:8080"
    client.get("/", headers={**page, "host": "photos.example:443"})
    assert state.last_origin == "http://photos.example:443"


def test_unauthenticated_page_load_does_not_set_origin(api: tuple[TestClient, AppState]) -> None:
    client, state = api
    state.settings.set_many({"admin_password_hash": "scrypt$00$00"})
    page = {"host": "evil.lan", "sec-fetch-mode": "navigate", "accept": "text/html"}
    assert client.get("/api/status", headers=page).status_code == 401
    assert state.last_origin is None


def test_metrics_keep_same_named_libraries_apart(api: tuple[TestClient, AppState]) -> None:
    client, state = api
    ids: list[str] = []
    with state.store.session() as s:
        for n in (1, 2):
            account = repo.create_account(
                s, display_name=f"a{n}", apple_id=f"{n}@b.c", region="cn",
                password_enc=b"x", client_id=f"c{n}",
            )
            lib = repo.upsert_library(
                s, account, kind="personal", zone_name="PrimarySync", display_name="Photos",
                target_dir=f"p{n}", interval_min=60, nightly_at="03:00",
            )
            lib.stats_json = json.dumps({"files": n})
            s.flush()
            ids.append(lib.id)
    text = client.get("/metrics").text
    for n, library_id in enumerate(ids, start=1):
        assert f'i2n_library_files{{library_id="{library_id}",library="Photos"}} {n}' in text


def test_mutating_api_requests_must_send_json(api: tuple[TestClient, AppState]) -> None:
    client, state = api
    form = client.post(
        "/api/pause", content="a=1", headers={"content-type": "application/x-www-form-urlencoded"}
    )
    assert form.status_code == 415
    assert form.json() == {"error": "unsupported media type", "detail": "send application/json"}
    assert client.post("/api/pause").status_code == 415  # no body, no content type
    assert client.request("DELETE", "/api/session").status_code == 415
    assert client.post("/api/session", content=b'{"password": ""}').status_code == 415
    assert state.settings.get("paused") is False
    ok = client.post(
        "/api/pause", content=b"{}", headers={"content-type": "application/json; charset=utf-8"}
    )
    assert ok.status_code == 200 and state.settings.get("paused") is True
    assert client.get("/api/status").status_code == 200  # reads are unaffected
