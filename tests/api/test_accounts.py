from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from icloud2nas.api.state import AppState
from icloud2nas.orchestrator.store import repo
from icloud2nas.orchestrator.store.models import Account
from tests.api.conftest import onboard, set_scenario, wait_for

API = tuple[TestClient, AppState]


def _status(client: TestClient, account_id: str) -> str:
    response = client.get(f"/api/accounts/{account_id}")
    assert response.status_code == 200, response.text
    return str(response.json()["status"])


def test_create_account_starts_auth_and_code_completes_onboarding(
    api: API, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _ = api
    set_scenario(
        monkeypatch,
        {
            "auth": {
                "events": [
                    {"ev": "started"},
                    {"ev": "need_mfa"},
                    {"wait_code": "123456"},
                    {"ev": "mfa_result", "ok": True},
                    {"ev": "done", "stats": {}},
                ],
                "exit": 0,
            }
        },
    )
    response = client.post(
        "/api/accounts",
        json={
            "display_name": "旭刚",
            "apple_id": "a@icloud.com",
            "region": "cn",
            "password": "pw",
            "target_dir": "Xugang",
        },
    )
    assert response.status_code == 201
    account_id = response.json()["id"]
    assert response.json()["run_id"]
    wait_for(client, lambda c: _status(c, account_id) == "awaiting_code")
    code = client.post(f"/api/accounts/{account_id}/code", json={"code": "123456"})
    assert code.status_code == 204
    wait_for(client, lambda c: _status(c, account_id) == "ok")
    libraries = wait_for(client, lambda c: c.get("/api/libraries").json())
    assert [lib["target_dir"] for lib in libraries] == ["Xugang"]
    assert client.get(f"/api/accounts/{account_id}").json()["library_ids"] == [libraries[0]["id"]]
    assert [a["id"] for a in client.get("/api/accounts").json()] == [account_id]
    assert "password" not in response.text


def test_code_without_session_is_409(api: API) -> None:
    client, _ = api
    account_id, _ = onboard(client)
    wait_for(client, lambda c: _status(c, account_id) == "ok")
    response = client.post(f"/api/accounts/{account_id}/code", json={"code": "1"})
    assert response.status_code == 409
    assert response.json()["error"] == "busy"
    assert client.post("/api/accounts/nope/code", json={"code": "1"}).status_code == 404


def test_create_account_rejects_bad_target_dir(api: API) -> None:
    client, state = api
    response = client.post(
        "/api/accounts",
        json={
            "display_name": "x",
            "apple_id": "x@icloud.com",
            "region": "cn",
            "password": "pw",
            "target_dir": "../x",
        },
    )
    assert response.status_code == 400
    with state.store.session() as s:
        assert list(s.scalars(select(Account))) == []


def test_duplicate_apple_id_is_409(api: API) -> None:
    client, _ = api
    onboard(client)
    response = client.post(
        "/api/accounts",
        json={"display_name": "dup", "apple_id": "a@icloud.com", "region": "cn", "password": "p"},
    )
    assert response.status_code == 409


def test_patch_password_restarts_auth(api: API) -> None:
    client, state = api
    account_id, _ = onboard(client)
    wait_for(client, lambda c: _status(c, account_id) == "ok")
    response = client.patch(
        f"/api/accounts/{account_id}", json={"password": "new", "display_name": " Renamed "}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["display_name"] == "Renamed"
    assert body["run_id"]
    with state.store.session() as s:
        run = repo.get_run(s, body["run_id"])
        assert run is not None and run.mode == "auth"
    wait_for(client, lambda c: _status(c, account_id) == "ok" and not state.ops.discovering)


def test_reauth_and_refresh(api: API, monkeypatch: pytest.MonkeyPatch) -> None:
    client, state = api
    account_id, _ = onboard(client)
    wait_for(client, lambda c: _status(c, account_id) == "ok")
    reauth = client.post(f"/api/accounts/{account_id}/auth", json={})
    assert reauth.status_code == 202 and reauth.json()["run_id"]
    wait_for(client, lambda c: _status(c, account_id) == "ok")
    wait_for(client, lambda c: not state.ops.discovering)
    refreshed = client.post(f"/api/accounts/{account_id}/refresh", json={})
    assert refreshed.json() == {"personal": ["PrimarySync"], "shared": []}
    set_scenario(monkeypatch, {"list": {"events": [{"ev": "started"}], "exit": 1}})
    failed = client.post(f"/api/accounts/{account_id}/refresh", json={})
    assert failed.status_code == 502
    assert failed.json()["error"] == "worker failed"


def test_delete_account_keep_dir_flag(api: API) -> None:
    client, state = api
    account_id, _ = onboard(client, name="Ann", apple_id="ann@icloud.com")
    (state.config.photos_dir / "Ann").mkdir(parents=True, exist_ok=True)
    (state.config.photos_dir / "Ann" / "a.jpg").write_bytes(b"x")
    wait_for(client, lambda c: _status(c, account_id) == "ok")
    gone = client.request("DELETE", f"/api/accounts/{account_id}?keep_dir=false", json={})
    assert gone.status_code == 204
    assert client.get(f"/api/accounts/{account_id}").status_code == 404
    assert not (state.config.photos_dir / "Ann").exists()
    assert list((state.config.photos_dir / ".trash").rglob("a.jpg"))


def test_delete_account_keeps_dir_by_default(api: API) -> None:
    client, state = api
    account_id, _ = onboard(client, name="Ann", apple_id="ann@icloud.com")
    (state.config.photos_dir / "Ann").mkdir(parents=True, exist_ok=True)
    wait_for(client, lambda c: _status(c, account_id) == "ok")
    assert client.request("DELETE", f"/api/accounts/{account_id}", json={}).status_code == 204
    assert (state.config.photos_dir / "Ann").is_dir()
    assert client.get("/api/libraries").json() == []


def test_prefix_ids_resolve(api: API) -> None:
    client, _ = api
    account_id, library_id = onboard(client)
    assert client.get("/api/accounts/a@icloud.com").json()["id"] == account_id
    assert client.get("/api/accounts/旭刚").json()["id"] == account_id
    assert client.get(f"/api/accounts/{account_id[:8]}").json()["id"] == account_id
    assert client.get(f"/api/libraries/{library_id[:8]}").json()["id"] == library_id
    assert client.get("/api/libraries/旭刚").json()["id"] == library_id
    assert client.get("/api/accounts/zzzz").status_code == 404


def test_account_is_busy_while_discovery_runs(api: API, monkeypatch: pytest.MonkeyPatch) -> None:
    client, state = api
    account_id, _ = onboard(client)
    wait_for(client, lambda c: _status(c, account_id) == "ok" and not state.ops.discovering)
    set_scenario(
        monkeypatch,
        {
            "list": {
                "events": [
                    {"ev": "started"},
                    {"sleep": 1},
                    {"ev": "libraries", "personal": ["PrimarySync"], "shared": []},
                    {"ev": "done", "stats": {}},
                ],
                "exit": 0,
            }
        },
    )
    assert client.post(f"/api/accounts/{account_id}/auth", json={}).status_code == 202
    wait_for(client, lambda c: _status(c, account_id) == "ok")
    wait_for(client, lambda c: state.ops.discovering)
    assert account_id in state.scheduler.running_accounts
    assert client.post(f"/api/accounts/{account_id}/refresh", json={}).status_code == 409
    assert client.post(f"/api/accounts/{account_id}/auth", json={}).status_code == 409
    assert client.request("DELETE", f"/api/accounts/{account_id}", json={}).status_code == 409
    wait_for(client, lambda c: not state.ops.discovering)
    assert account_id not in state.scheduler.running_accounts
    assert client.post(f"/api/accounts/{account_id}/refresh", json={}).status_code == 200
    assert client.request("DELETE", f"/api/accounts/{account_id}", json={}).status_code == 204


def test_blank_display_name_rejected(api: API) -> None:
    client, _ = api
    account_id, _ = onboard(client)
    response = client.patch(f"/api/accounts/{account_id}", json={"display_name": "  "})
    assert response.status_code == 400
    assert client.get(f"/api/accounts/{account_id}").json()["display_name"] == "旭刚"
