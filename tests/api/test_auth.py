from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from icloud2nas.api.auth import (
    COOKIE,
    SETUP_REQUIRED,
    hash_password,
    is_remote,
    sign_session,
    verify_password,
    verify_session,
)
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


def test_lan_mode_is_open(api: tuple[TestClient, AppState]) -> None:
    client, _ = api
    assert client.get("/api/session").json() == {
        "mode": "lan", "auth_required": False, "authenticated": False, "remote": False,
    }
    assert client.get("/api/status").status_code == 200


def _assert_needs_setup(client: TestClient) -> None:
    gated = client.get("/api/status")
    assert gated.status_code == 428
    assert gated.json() == {"detail": SETUP_REQUIRED}
    assert client.get("/healthz").status_code == 200
    assert client.get("/metrics").status_code == 200
    session = client.get("/api/session").json()
    assert session["mode"] == "setup"
    assert session["auth_required"] is False


def test_fresh_and_passwordless_installs_need_setup(api: tuple[TestClient, AppState]) -> None:
    client, state = api
    state.settings.set("access_mode", "")
    _assert_needs_setup(client)
    # an install upgraded from before access modes: no password, nothing chosen
    state.settings.set("access_mode", "lan")
    state.settings.set_many({"admin_password_hash": "", "access_mode": ""})
    _assert_needs_setup(client)


def test_password_means_wan_even_if_lan_flag_set(api: tuple[TestClient, AppState]) -> None:
    client, state = api
    assert state.settings.get("access_mode") == "lan"
    _set_password(state)
    assert client.get("/api/session").json()["mode"] == "wan"
    assert client.get("/api/status").status_code == 401


def test_login_refused_unless_wan(api: tuple[TestClient, AppState]) -> None:
    client, state = api
    for mode in ("lan", ""):
        state.settings.set("access_mode", mode)
        response = client.post("/api/session", json={"password": "x"})
        assert response.status_code == 400
        assert "set-cookie" not in response.headers


@pytest.mark.parametrize(
    ("headers", "client_host", "remote"),
    [
        ({"forwarded": "for=1.2.3.4"}, "192.168.1.5", True),
        ({"x-forwarded-for": "1.2.3.4"}, "10.0.0.2", True),
        ({"x-real-ip": "1.2.3.4"}, "127.0.0.1", True),
        ({}, "8.8.8.8", True),
        ({}, "192.168.1.5", False),
        ({}, "10.0.0.2", False),
        ({}, "127.0.0.1", False),
        ({}, "::1", False),
        ({}, "testclient", False),
    ],
)
def test_is_remote(headers: dict[str, str], client_host: str, remote: bool) -> None:
    request = Request(
        {
            "type": "http",
            "headers": [(k.encode(), v.encode()) for k, v in headers.items()],
            "client": (client_host, 1),
        }
    )
    assert is_remote(request) is remote


def test_session_reports_remote(api: tuple[TestClient, AppState]) -> None:
    client, _ = api
    body = client.get("/api/session", headers={"x-forwarded-for": "1.2.3.4"}).json()
    assert body["remote"] is True


def test_password_gates_api_but_not_health(api: tuple[TestClient, AppState]) -> None:
    client, state = api
    _set_password(state)
    assert client.get("/api/status").status_code == 401
    assert client.get("/api/status").json() == {"detail": {"error": "unauthorized"}}
    assert client.get("/healthz").status_code == 200
    assert client.get("/metrics").status_code == 200
    assert client.get("/api/session").json() == {
        "mode": "wan", "auth_required": True, "authenticated": False, "remote": False,
    }


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
    assert client.get("/api/session").json() == {
        "mode": "wan", "auth_required": True, "authenticated": True, "remote": False,
    }
    assert client.request("DELETE", "/api/session", json={}).status_code == 204
    assert client.get("/api/status").status_code == 401


@pytest.mark.parametrize("cookie", ["1.é", "junk.", "é", "1."])
def test_malformed_cookie_is_unauthorized(api: tuple[TestClient, AppState], cookie: str) -> None:
    client, state = api
    _set_password(state)
    headers = {"cookie": f"i2n_session={cookie}".encode()}
    assert client.get("/api/status", headers=headers).status_code == 401
    body = client.get("/api/session", headers=headers).json()
    assert body == {
        "mode": "wan", "auth_required": True, "authenticated": False, "remote": False,
    }


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


def test_serve_runs_on_the_stdlib_event_loop(
    config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    # uvloop's subprocess_exec rejects user=/group=/extra_groups=, which workers need
    import uvicorn

    from icloud2nas.main import serve

    for key, value in make_env(config).items():
        monkeypatch.setenv(key, value)
    seen: dict[str, object] = {}
    monkeypatch.setattr(uvicorn, "run", lambda *args, **kwargs: seen.update(kwargs))
    assert serve([]) == 0
    assert seen["loop"] == "asyncio"


def test_reset_admin_password_flag(
    config: Config, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import uvicorn

    from icloud2nas.main import create_app, serve

    env = make_env(config)
    for key, value in env.items():
        monkeypatch.setenv(key, value)

    def fail(*args: object, **kwargs: object) -> None:
        raise AssertionError("uvicorn.run must not be called")

    monkeypatch.setattr(uvicorn, "run", fail)
    with TestClient(create_app(config, env)) as client:
        state0: AppState = client.app.state.i2n  # type: ignore[attr-defined]
        state0.settings.set("access_mode", "lan")  # a bare create_app starts in setup
        put = client.put("/api/settings", json={"admin_password": PASSWORD})
        assert put.status_code == 200
        login(client)
        assert client.get("/api/status").status_code == 200
        old_cookie = sign_session(state0.secrets.derive("i2n-session"), int(time.time()) - 5)
    before = int(time.time())
    assert serve(["--reset-admin-password"]) == 0
    assert capsys.readouterr().out.strip() == (
        "admin password cleared; open the web UI to choose an access mode"
    )
    with TestClient(create_app(config, env)) as client:
        state: AppState = client.app.state.i2n  # type: ignore[attr-defined]
        assert state.settings.get("admin_password_hash") == ""
        assert state.settings.get("access_mode") == ""
        assert state.settings.get("admin_password_set_at") >= before
        assert client.get("/api/session").json()["mode"] == "setup"
        # a cookie issued before the reset does not come back to life under a new password
        _set_password(state, set_at=int(time.time()))
        client.cookies.set(COOKIE, old_cookie)
        assert client.get("/api/status").status_code == 401
    lock = DaemonLock(config.lock_path)
    lock.acquire("icloud2nas-serve pid 1")
    try:
        assert serve(["--reset-admin-password"]) == 1
    finally:
        lock.release()
    assert "holds the worker lock" in capsys.readouterr().err
