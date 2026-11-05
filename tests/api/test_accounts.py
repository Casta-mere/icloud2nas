from __future__ import annotations

import dataclasses
import json
import os
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from icloud2nas.api.state import AppState
from icloud2nas.orchestrator import owners
from icloud2nas.orchestrator.store import repo
from icloud2nas.orchestrator.store.models import Account
from icloud2nas.orchestrator.supervisor import WorkerProcess
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
            "display_name": "小明",
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


def _break_spawn(state: AppState, monkeypatch: pytest.MonkeyPatch, how: str) -> None:
    if how == "worker":  # the real WorkerProcess.start opens the run log, then the exec fails
        broken = dataclasses.replace(state.config, worker_cmd=("/definitely/not/a/binary",))
        monkeypatch.setattr(state.onboarding, "config", broken)
    else:

        def denied(*_: Any, **__: Any) -> None:
            raise PermissionError(13, "Permission denied", "/data/accounts")

        monkeypatch.setattr(owners, "prepare_spawn", denied)


@pytest.mark.parametrize("how", ["worker", "prepare_spawn"])
def test_create_account_rolls_back_when_the_login_cannot_start(
    api: API, monkeypatch: pytest.MonkeyPatch, how: str
) -> None:
    client, state = api
    config = state.config
    photos = config.photos_dir / "Xugang"  # the user's folder may already hold photos
    photos.mkdir()
    (photos / "IMG_0001.JPG").write_bytes(b"jpeg")
    seen: list[tuple[str, dict[str, Any]]] = []
    state.bus.subscribe(lambda topic, payload: seen.append((topic, payload)))
    body = {
        "display_name": "小明", "apple_id": "a@icloud.com", "region": "cn", "password": "pw",
        "target_dir": "Xugang",
    }
    with monkeypatch.context() as m:
        _break_spawn(state, m, how)
        failed = client.post("/api/accounts", json=body)
    assert failed.status_code == 500, failed.text
    assert failed.json()["error"] == "login failed to start"
    assert failed.json()["detail"]
    with state.store.session() as s:
        assert repo.list_accounts(s) == []
        assert repo.list_libraries(s) == [] and repo.list_runs(s) == []
    assert list(config.accounts_dir.iterdir()) == []
    assert list(config.plans_dir.iterdir()) == []
    assert list(config.logs_dir.iterdir()) == []
    assert (photos / "IMG_0001.JPG").read_bytes() == b"jpeg"
    updated = [p for t, p in seen if t == "account.updated"]
    assert updated[-1] == {"account_id": updated[0]["account_id"], "deleted": True}

    retried = client.post("/api/accounts", json=body)
    assert retried.status_code == 201, retried.text
    account_id = retried.json()["id"]
    wait_for(client, lambda c: _status(c, account_id) == "ok" and not state.ops.discovering)
    assert (photos / "IMG_0001.JPG").read_bytes() == b"jpeg"


def test_rollback_reports_even_when_a_run_log_cannot_be_removed(
    api: API, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, state = api
    config = state.config
    seen: list[tuple[str, dict[str, Any]]] = []
    state.bus.subscribe(lambda topic, payload: seen.append((topic, payload)))
    real_unlink = Path.unlink

    def denied(self: Path, missing_ok: bool = False) -> None:
        if self.parent == config.logs_dir:  # say the logs dir lost its search permission
            raise PermissionError(13, "Permission denied", str(self))
        real_unlink(self, missing_ok=missing_ok)

    _break_spawn(state, monkeypatch, "worker")  # the real start opens a run log, then fails
    monkeypatch.setattr(Path, "unlink", denied)
    failed = client.post(
        "/api/accounts",
        json={"display_name": "小明", "apple_id": "a@icloud.com", "region": "cn", "password": "pw"},
    )
    assert failed.status_code == 500, failed.text
    assert failed.json()["error"] == "login failed to start"
    with state.store.session() as s:
        assert repo.list_accounts(s) == [] and repo.list_runs(s) == []
    assert list(config.accounts_dir.iterdir()) == []
    assert list(config.plans_dir.iterdir()) == []
    assert len(list(config.logs_dir.iterdir())) == 1  # only the log it could not remove
    updated = [p for t, p in seen if t == "account.updated"]
    assert updated[-1] == {"account_id": updated[0]["account_id"], "deleted": True}


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
    assert list((state.config.photos_dir / ".icloud2nas-deleted").rglob("a.jpg"))
    assert not (state.config.photos_dir / ".trash").exists()


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
    assert client.get("/api/accounts/小明").json()["id"] == account_id
    assert client.get(f"/api/accounts/{account_id[:8]}").json()["id"] == account_id
    assert client.get(f"/api/libraries/{library_id[:8]}").json()["id"] == library_id
    assert client.get("/api/libraries/小明").json()["id"] == library_id
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
    assert client.get(f"/api/accounts/{account_id}").json()["display_name"] == "小明"


def _owner(client: TestClient, account_id: str) -> tuple[object, object, object]:
    body = client.get(f"/api/accounts/{account_id}").json()
    return body["owner_uid"], body["owner_gid"], body["owner_name"]


@pytest.mark.skipif(os.geteuid() != 0, reason="spawning workers as another user needs root")
def test_create_account_defaults_owner_from_folder(api: API) -> None:
    client, state = api
    home = state.config.photos_dir / "Bob"
    home.mkdir()
    os.chown(home, 1001, 10)
    account_id, _ = onboard(client, target_dir="Bob/Photos/iCloud")
    assert _owner(client, account_id) == (1001, 10, None)  # no /host/etc: no name
    account_dir = state.config.accounts_dir / account_id
    assert (account_dir.stat().st_uid, account_dir.stat().st_gid) == (1001, 10)
    with state.store.session() as s:
        runs = repo.list_runs(s, account_id=account_id)
        assert runs and all(r.status == "ok" for r in runs)  # the workers ran as 1001


def test_owner_given_on_create_and_patch_both_or_neither(
    api: API, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _ = api
    monkeypatch.setattr(owners, "privileged", lambda: False)
    account_id, _ = onboard(client, owner_uid=1003, owner_gid=100)
    assert _owner(client, account_id) == (1003, 100, None)
    one = client.patch(f"/api/accounts/{account_id}", json={"owner_uid": 1000})
    assert one.status_code == 400 and one.json()["error"] == "invalid"
    neg = client.patch(f"/api/accounts/{account_id}", json={"owner_uid": -1, "owner_gid": 10})
    assert neg.status_code == 400
    same = client.patch(f"/api/accounts/{account_id}", json={"owner_uid": 1003, "owner_gid": 100})
    assert same.status_code == 200  # repeating the stored owner is a no-op
    assert (same.json()["owner_uid"], same.json()["owner_gid"]) == (1003, 100)
    bad = client.post(
        "/api/accounts",
        json={"display_name": "y", "apple_id": "y@icloud.com", "region": "cn",
              "password": "pw", "owner_gid": 10},
    )
    assert bad.status_code == 400


def test_single_user_mode_ignores_owner(api: API, monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = api
    monkeypatch.setattr(owners, "privileged", lambda: False)
    spawned: list[tuple[int, int] | None] = []
    real_init = WorkerProcess.__init__

    def spy(self: WorkerProcess, *args: Any, **kwargs: Any) -> None:
        spawned.append(kwargs.get("owner"))
        real_init(self, *args, **kwargs)

    monkeypatch.setattr(WorkerProcess, "__init__", spy)
    account_id, _ = onboard(client, owner_uid=1003, owner_gid=100)
    assert _owner(client, account_id)[:2] == (1003, 100)  # stored, shown, not used
    assert spawned and all(owner is None for owner in spawned)
    created = client.post(
        "/api/accounts",
        json={"display_name": "z", "apple_id": "z@icloud.com", "region": "cn", "password": "pw",
              "target_dir": "Z"},
    )
    assert created.status_code == 201
    assert _owner(client, created.json()["id"]) == (None, None, None)  # no default either



@pytest.mark.parametrize("privileged", [True, False])
def test_owner_cannot_change_after_the_account_is_added(
    api: API, monkeypatch: pytest.MonkeyPatch, privileged: bool
) -> None:
    client, state = api
    monkeypatch.setattr(owners, "privileged", lambda: False)  # the fake worker runs as us
    account_id, _ = onboard(client, owner_uid=1003, owner_gid=100)
    unset_id, _ = onboard(client, name="小红", apple_id="b@icloud.com", target_dir="B")
    with state.store.session() as s:  # an account added before owners existed
        legacy = repo.get_account(s, unset_id)
        legacy.owner_uid = legacy.owner_gid = None
    monkeypatch.setattr(owners, "privileged", lambda: privileged)
    chowned: list[object] = []
    monkeypatch.setattr(owners, "chown_tree", lambda *a, **k: chowned.append(a))
    for target, before in ((account_id, (1003, 100, None)), (unset_id, (None, None, None))):
        refused = client.patch(
            f"/api/accounts/{target}",
            json={"display_name": "new", "owner_uid": 1000, "owner_gid": 10},
        )
        assert refused.status_code == 409, refused.text
        assert refused.json()["error"] == "owner fixed"
        assert "cannot be changed" in refused.json()["detail"]
        assert _owner(client, target) == before
        assert client.get(f"/api/accounts/{target}").json()["display_name"] != "new"
    assert chowned == []


def test_renew_starts_a_renewing_auth_run_without_discovery(
    api: API, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    client, state = api
    account_id, _ = onboard(client)
    wait_for(client, lambda c: _status(c, account_id) == "ok" and not state.ops.discovering)
    before = client.get(f"/api/accounts/{account_id}").json()
    assert before["session_expires_at"] is None and before["session_expiry_estimated"] is False
    calls = tmp_path / "calls.jsonl"
    monkeypatch.setenv("FAKE_WORKER_CALLS", str(calls))
    set_scenario(
        monkeypatch,
        {
            "auth": {
                "events": [
                    {"ev": "started"},
                    {"ev": "need_mfa"},
                    {"wait_code": "123456"},
                    {"ev": "mfa_result", "ok": True},
                    {"ev": "session", "trust_expires": "2027-01-19T09:12:13+00:00"},
                    {"ev": "done", "stats": {}},
                ],
                "exit": 0,
            }
        },
    )
    renew = client.post(f"/api/accounts/{account_id}/renew", json={})
    assert renew.status_code == 202 and renew.json()["run_id"]
    wait_for(client, lambda c: _status(c, account_id) == "awaiting_code")
    assert client.post(f"/api/accounts/{account_id}/renew", json={}).status_code == 409
    assert client.post(f"/api/accounts/{account_id}/auth", json={}).status_code == 409
    code = client.post(f"/api/accounts/{account_id}/code", json={"code": "123456"})
    assert code.status_code == 204
    wait_for(client, lambda c: _status(c, account_id) == "ok")
    after = client.get(f"/api/accounts/{account_id}").json()
    assert after["session_expires_at"] == "2027-01-19T09:12:13+00:00"
    assert after["session_expiry_estimated"] is False
    jobs = [json.loads(line) for line in calls.read_text().splitlines()]
    assert [(j["argv"][1], j["job"]["renew"], j["job"]["interactive"]) for j in jobs] == [
        ("auth", True, True)
    ]  # no list run: a renewal is not followed by library discovery
    assert not state.ops.discovering and account_id not in state.scheduler.running_accounts
    run = client.get(f"/api/runs/{renew.json()['run_id']}").json()
    assert run["mode"] == "auth" and run["status"] == "ok"


def test_renew_unknown_account_is_404(api: API) -> None:
    client, _ = api
    assert client.post("/api/accounts/zzzz/renew", json={}).status_code == 404


def test_an_abandoned_renewal_keeps_the_account_syncing(
    api: API, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, state = api
    account_id, _ = onboard(client)
    wait_for(client, lambda c: _status(c, account_id) == "ok" and not state.ops.discovering)
    set_scenario(
        monkeypatch,
        {
            "auth": {
                "events": [
                    {"ev": "started"},
                    {"ev": "need_mfa"},
                    {"ev": "error", "kind": "mfa_timeout", "message": "no code within 600s"},
                ],
                "exit": 1,
            }
        },
    )
    run_id = client.post(f"/api/accounts/{account_id}/renew", json={}).json()["run_id"]
    wait_for(client, lambda c: c.get(f"/api/runs/{run_id}").json()["status"] == "failed")
    wait_for(client, lambda c: account_id not in state.onboarding.sessions)
    account = client.get(f"/api/accounts/{account_id}").json()
    assert account["status"] == "ok" and account["failures"] == 0
    assert account["backoff_until"] is None and account["last_error"] is None
