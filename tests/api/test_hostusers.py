from __future__ import annotations

import dataclasses
import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from icloud2nas.api.state import AppState, build_state
from icloud2nas.main import create_app
from icloud2nas.orchestrator import owners
from icloud2nas.orchestrator.config import Config
from tests.api.conftest import onboard
from tests.conftest import make_env
from tests.orchestrator.test_owners import GROUP, PASSWD

API = tuple[TestClient, AppState]


@pytest.fixture
def host_api(config: Config, tmp_path: Path) -> Iterator[API]:
    etc = tmp_path / "host-etc"
    etc.mkdir()
    (etc / "passwd").write_text(PASSWD, encoding="utf-8")
    (etc / "group").write_text(GROUP, encoding="utf-8")
    cfg = dataclasses.replace(config, host_etc=etc)
    env = make_env(cfg)
    state = build_state(cfg, env)
    with TestClient(create_app(cfg, env, state=state)) as client:
        yield client, state


def test_host_users_endpoint_privileged_flag(
    host_api: API, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _ = host_api
    monkeypatch.setattr(owners, "privileged", lambda: True)
    body = client.get("/api/host-users").json()
    assert body["privileged"] is True and body["suggested"] is None
    assert body["users"] == [
        {"name": "Castamere", "uid": 1000, "gid": 10, "group": "admin",
         "home": "/home/Castamere"},
        {"name": "Today_red", "uid": 1001, "gid": 10, "group": "admin",
         "home": "/home/Today_red"},
        {"name": "WY", "uid": 1003, "gid": 100, "group": "100", "home": "/home/WY"},
    ]
    monkeypatch.setattr(owners, "privileged", lambda: False)
    assert client.get("/api/host-users").json()["privileged"] is False


def test_host_users_suggests_the_folder_owner(host_api: API) -> None:
    client, state = host_api
    (state.config.photos_dir / "WY").mkdir()
    st = os.stat(state.config.photos_dir / "WY")
    body = client.get("/api/host-users", params={"path": "WY/Photos/iCloud"}).json()
    assert body["suggested"] == {"uid": st.st_uid, "gid": st.st_gid}
    assert client.get("/api/host-users", params={"path": "../etc"}).status_code == 400
    assert client.get("/api/host-users", params={"path": "/etc"}).status_code == 400


def test_host_users_empty_without_host_etc(api: API) -> None:
    client, _ = api
    body = client.get("/api/host-users").json()
    assert body["users"] == [] and isinstance(body["privileged"], bool)


def test_account_json_names_the_owner(host_api: API, monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = host_api
    monkeypatch.setattr(owners, "privileged", lambda: False)
    account_id, _ = onboard(client, owner_uid=1001, owner_gid=10)
    body = client.get(f"/api/accounts/{account_id}").json()
    assert body["owner_name"] == "Today_red"
    listed = client.get("/api/accounts").json()
    assert listed[0]["owner_name"] == "Today_red"

