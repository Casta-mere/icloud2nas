from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from icloud2nas.api.auth import hash_password, sign_session, verify_password, verify_session
from icloud2nas.api.state import AppState
from icloud2nas.orchestrator.config import Config
from icloud2nas.orchestrator.lock import DaemonLock

from .conftest import PASSWORD, login, make_env


def _set_password(state: AppState, set_at: int | None = None) -> None:
    state.settings.set_many(
        {
            "admin_password_hash": hash_password(PASSWORD),
            "admin_password_set_at": str(set_at if set_at is not None else int(time.time()) - 5),
        }
    )


def test_errors_are_mapped(api: tuple[TestClient, AppState]) -> None:
    client, state = api

    @client.app.get("/api/_boom/{kind}")  # type: ignore[attr-defined]
    async def boom(kind: str) -> None:
        raise {"key": KeyError("nope"), "value": ValueError("bad")}[kind]

    response = client.get("/api/_boom/key")
    assert response.status_code == 404
    assert response.json()["error"] == "not found"
    assert client.get("/api/_boom/value").status_code == 400


def test_no_password_means_open(api: tuple[TestClient, AppState]) -> None:
    client, _ = api
    assert client.get("/api/session").json() == {"auth_required": False, "authenticated": False}
    assert client.get("/api/status").status_code == 200


def test_password_gates_api_but_not_health(api: tuple[TestClient, AppState]) -> None:
    client, state = api
    _set_password(state)
    assert client.get("/api/status").status_code == 401
    assert client.get("/api/status").json() == {"detail": {"error": "unauthorized"}}
    assert client.get("/healthz").status_code == 200
    assert client.get("/metrics").status_code == 200
    assert client.get("/api/session").json() == {"auth_required": True, "authenticated": False}


def test_login_sets_cookie_and_logout_clears(api: tuple[TestClient, AppState]) -> None:
    client, state = api
    _set_password(state)
    bad = client.post("/api/session", json={"password": "wrong"})
    assert bad.status_code == 401
    assert "set-cookie" not in bad.headers
    good = client.post("/api/session", json={"password": PASSWORD})
    assert good.status_code == 204
    cookie = good.headers["set-cookie"].lower()
    assert "i2n_session=" in cookie and "httponly" in cookie and "samesite=lax" in cookie
    assert "path=/" in cookie
    assert client.get("/api/status").status_code == 200
    assert client.get("/api/session").json() == {"auth_required": True, "authenticated": True}
    assert client.delete("/api/session").status_code == 204
    assert client.get("/api/status").status_code == 401


@pytest.mark.parametrize("cookie", ["1.é", "junk.", "é", "1."])
def test_malformed_cookie_is_unauthorized(api: tuple[TestClient, AppState], cookie: str) -> None:
    client, state = api
    _set_password(state)
    headers = {"cookie": f"i2n_session={cookie}".encode()}
    assert client.get("/api/status", headers=headers).status_code == 401
    body = client.get("/api/session", headers=headers).json()
    assert body == {"auth_required": True, "authenticated": False}


def test_password_change_invalidates_old_cookie(api: tuple[TestClient, AppState]) -> None:
    client, state = api
    _set_password(state)
    login(client)
    assert client.get("/api/status").status_code == 200
    _set_password(state, set_at=int(time.time()) + 10)
    assert client.get("/api/status").status_code == 401


def test_hash_and_sign_helpers() -> None:
    stored = hash_password("pw")
    assert stored.startswith("scrypt$")
    assert verify_password("pw", stored)
    assert not verify_password("nope", stored)
    assert not verify_password("pw", "garbage")
    key = b"k" * 32
    token = sign_session(key, 1000)
    assert verify_session(key, token, not_before=0, now=1500)
    assert not verify_session(key, token, not_before=1001, now=1500)
    assert not verify_session(key, token, not_before=0, max_age_s=100, now=1500)
    assert not verify_session(b"other", token, not_before=0, now=1500)
    assert not verify_session(key, "1000.deadbeef", not_before=0, now=1500)
    assert not verify_session(key, "junk", not_before=0, now=1500)


def test_serve_refuses_when_lock_held(
    config: Config, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import uvicorn

    from icloud2nas.main import serve

    for key, value in make_env(config).items():
        monkeypatch.setenv(key, value)

    def fail(*args: object, **kwargs: object) -> None:
        raise AssertionError("uvicorn.run must not be called")

    monkeypatch.setattr(uvicorn, "run", fail)
    lock = DaemonLock(config.lock_path)
    lock.acquire("someone")
    try:
        assert serve([]) == 1
    finally:
        lock.release()
    assert "holds the worker lock" in capsys.readouterr().err
