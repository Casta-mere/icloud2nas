from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from icloud2nas.api.state import AppState
from tests.api.conftest import onboard, run_manual, set_scenario, wait_for

API = tuple[TestClient, AppState]


def _ok(client: TestClient, account_id: str) -> bool:
    return bool(client.get(f"/api/accounts/{account_id}").json()["status"] == "ok")


def test_library_patch_sync_pause(api: API) -> None:
    client, state = api
    account_id, library_id = onboard(client)
    wait_for(client, lambda c: _ok(c, account_id))
    patched = client.patch(
        f"/api/libraries/{library_id}", json={"interval_min": 15, "nightly_at": "3:00"}
    )
    assert patched.status_code == 200
    assert patched.json()["interval_min"] == 15
    assert patched.json()["nightly_at"] == "03:00"
    assert "stats" in patched.json() and "first_full_at" in patched.json()
    assert client.patch(f"/api/libraries/{library_id}", json={"interval_min": 1}).status_code == 400

    paused = client.post(f"/api/libraries/{library_id}/pause")
    assert paused.json()["status"] == "paused"
    sync = client.post(f"/api/libraries/{library_id}/sync")
    assert sync.status_code == 202 and sync.json() == {"queued": True}
    run_manual(client, state, {"sync"})
    runs = client.get(f"/api/runs?library_id={library_id}").json()
    assert runs and runs[0]["mode"] == "sync" and runs[0]["trigger"] == "manual"
    assert client.post(f"/api/libraries/{library_id}/resume").json()["status"] == "ok"


def test_sync_disabled_library_is_400_and_space_low_is_409(
    api: API, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, state = api
    account_id, library_id = onboard(client)
    wait_for(client, lambda c: _ok(c, account_id))
    monkeypatch.setattr(state.scheduler, "free_space_ok", lambda: False)
    low = client.post(f"/api/libraries/{library_id}/reconcile")
    assert low.status_code == 409
    assert low.json() == {"error": "busy", "detail": "space low"}
    monkeypatch.undo()
    off = client.patch(f"/api/libraries/{library_id}", json={"enabled": False})
    assert off.json()["enabled"] is False
    assert client.post(f"/api/libraries/{library_id}/sync").status_code == 400


def test_library_patch_target_dir_conflicts(api: API, monkeypatch: pytest.MonkeyPatch) -> None:
    client, state = api
    account_id, library_id = onboard(client, target_dir="Xugang")
    other_account, other_lib = onboard(client, name="Wife", apple_id="w@icloud.com")
    assert other_account != account_id
    wait_for(client, lambda c: len(c.get("/api/libraries").json()) == 2)
    nested = client.patch(f"/api/libraries/{other_lib}", json={"target_dir": "Xugang/sub"})
    assert nested.status_code == 400
    wait_for(client, lambda c: _ok(c, account_id) and _ok(c, other_account))
    set_scenario(monkeypatch, {"sync": {"events": [{"ev": "started"}, {"sleep": 2}], "exit": 0}})
    assert client.post(f"/api/libraries/{library_id}/sync").status_code == 202
    client.portal.call(state.scheduler.tick)  # type: ignore[union-attr]
    wait_for(client, lambda c: library_id in state.scheduler.running_libraries)
    busy = client.patch(f"/api/libraries/{library_id}", json={"target_dir": "Moved"})
    assert busy.status_code == 409 and busy.json()["error"] == "busy"
