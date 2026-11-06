from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from icloud2nas.api.state import AppState
from tests.api.conftest import PASSWORD, login

API = tuple[TestClient, AppState]


def test_settings_get_put_validation(api: API) -> None:
    client, state = api
    topics: list[tuple[str, dict[str, Any]]] = []
    state.bus.subscribe(lambda topic, payload: topics.append((topic, payload)))
    first = client.get("/api/settings").json()
    assert first["admin_password_set"] is False
    assert "admin_password_hash" not in first["values"]
    before = first["values"]

    bad = client.put("/api/settings", json={"max_workers": "9"})
    assert bad.status_code == 400
    assert client.get("/api/settings").json()["values"] == before

    good = client.put(
        "/api/settings", json={"max_workers": "3", "notify.space_low.enabled": "false"}
    )
    assert good.status_code == 200
    values = good.json()["values"]
    assert values["max_workers"] == 3
    assert values["notify.space_low.enabled"] is False
    assert ("settings.updated", {"keys": ["max_workers", "notify.space_low.enabled"]}) in topics

    typed = client.put("/api/settings", json={"max_workers": 4, "paused": True})
    assert typed.json()["values"]["max_workers"] == 4
    assert typed.json()["values"]["paused"] is True
    assert client.put("/api/settings", json={"nope": "1"}).status_code == 400


def test_admin_password_set_and_cleared(api: API) -> None:
    client, _ = api
    put = client.put("/api/settings", json={"admin_password": PASSWORD})
    assert put.status_code == 200
    assert put.json()["admin_password_set"] is True
    assert PASSWORD not in put.text
    assert "admin_password_hash" not in put.json()["values"]
    assert client.get("/api/settings").status_code == 401
    login(client)
    assert client.get("/api/settings").json()["admin_password_set"] is True
    cleared = client.put("/api/settings", json={"admin_password": ""})
    assert cleared.status_code == 200
    assert cleared.json()["admin_password_set"] is False
    client.cookies.clear()
    assert client.get("/api/settings").status_code == 200


def test_notify_test_endpoint(api: API, sent: list[tuple[list[str], str, str]]) -> None:
    client, _ = api
    empty = client.post("/api/notify/test", json={})
    assert empty.status_code == 400
    assert empty.json() == {"error": "invalid", "detail": "apprise_urls is empty"}
    assert sent == []
    client.put("/api/settings", json={"apprise_urls": "json://localhost\n\n"})
    response = client.post("/api/notify/test", json={})
    assert response.json() == {"sent": True}
    urls, title, body = sent[0]
    assert urls == ["json://localhost"]
    assert title == "icloud2nas 测试通知 / test notification"
    assert body.startswith("通知渠道工作正常。/ Notifications are working.")


def test_notify_test_sends_to_posted_urls_over_the_saved_ones(
    api: API, sent: list[tuple[list[str], str, str]]
) -> None:
    client, _ = api
    client.put(
        "/api/settings",
        json={"apprise_urls": "json://saved", "public_url": "https://photos.example"},
    )
    response = client.post(
        "/api/notify/test", json={"apprise_urls": " json://draft-a \n\n json://draft-b\n"}
    )
    assert response.status_code == 200
    assert response.json() == {"sent": True}
    urls, _, body = sent[0]
    assert urls == ["json://draft-a", "json://draft-b"]
    # the link still comes from the saved public_url, not from the posted body
    assert body.endswith("\nhttps://photos.example")


def test_notify_test_without_urls_in_the_body_uses_the_saved_ones(
    api: API, sent: list[tuple[list[str], str, str]]
) -> None:
    client, _ = api
    client.put("/api/settings", json={"apprise_urls": "json://saved"})
    assert client.post("/api/notify/test", json={}).json() == {"sent": True}
    assert client.post("/api/notify/test", json={"apprise_urls": None}).json() == {"sent": True}
    assert [urls for urls, _, _ in sent] == [["json://saved"], ["json://saved"]]


def test_notify_test_posted_blank_urls_are_empty_even_when_saved_ones_exist(
    api: API, sent: list[tuple[list[str], str, str]]
) -> None:
    client, _ = api
    client.put("/api/settings", json={"apprise_urls": "json://saved"})
    for blank in ("", "  \n\n \n"):
        response = client.post("/api/notify/test", json={"apprise_urls": blank})
        assert response.status_code == 400, repr(blank)
        assert response.json() == {"error": "invalid", "detail": "apprise_urls is empty"}
    assert sent == []


def test_notify_test_never_writes_settings(
    api: API, sent: list[tuple[list[str], str, str]]
) -> None:
    client, state = api
    client.put("/api/settings", json={"apprise_urls": "json://saved"})
    topics: list[str] = []
    state.bus.subscribe(lambda topic, payload: topics.append(topic))
    before = client.get("/api/settings").json()
    assert client.post("/api/notify/test", json={"apprise_urls": "json://draft"}).status_code == 200
    assert client.post("/api/notify/test", json={"apprise_urls": ""}).status_code == 400
    assert client.get("/api/settings").json() == before
    assert state.settings.get("apprise_urls") == "json://saved"
    assert "settings.updated" not in topics
    assert len(sent) == 1


def test_put_rejects_internal_keys(api: API) -> None:
    client, state = api
    client.put("/api/settings", json={"admin_password": PASSWORD})
    login(client)
    stored = {
        k: state.settings.get_raw(k) for k in ("admin_password_hash", "admin_password_set_at")
    }
    for key, value in (("admin_password_hash", "junk"), ("admin_password_set_at", "0")):
        response = client.put("/api/settings", json={key: value, "max_workers": "3"})
        assert response.status_code == 400, key
        assert state.settings.get("max_workers") == 2
    assert stored == {
        k: state.settings.get_raw(k) for k in ("admin_password_hash", "admin_password_set_at")
    }


def test_put_rejects_null_values(api: API) -> None:
    client, state = api
    assert client.put("/api/settings", json={"admin_password": None}).status_code == 400
    assert state.settings.get("admin_password_hash") == ""
    assert client.put("/api/settings", json={"admin_password": 5}).status_code == 400
    assert client.put("/api/settings", json={"max_workers": None}).status_code == 400
    assert state.settings.get("max_workers") == 2
