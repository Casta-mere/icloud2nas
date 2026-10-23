from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from httpx import Response

from icloud2nas.api.state import AppState
from icloud2nas.engine.trash import DAY_MARKER
from icloud2nas.orchestrator import owners
from icloud2nas.orchestrator.store import repo
from tests.api.conftest import onboard, set_scenario, wait_for

API = tuple[TestClient, AppState]


def _lib_dir(client: TestClient, state: AppState, library_id: str) -> Path:
    target = client.get(f"/api/libraries/{library_id}").json()["target_dir"]
    return state.config.photos_dir / target


def _seed(lib: Path, day: str, files: dict[str, bytes]) -> Path:
    day_dir = lib / ".icloud2nas" / "trash" / day
    day_dir.mkdir(parents=True, exist_ok=True)
    (day_dir / DAY_MARKER).touch()
    for name, data in files.items():
        path = day_dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return day_dir


def _delete(client: TestClient, day: str, library: str) -> Response:
    return client.request("DELETE", f"/api/trash/{day}", params={"library": library}, json={})


def test_trash_api_keyed_by_library(api: API) -> None:
    client, state = api
    _, library_id = onboard(client)
    lib = _lib_dir(client, state, library_id)
    _seed(lib, "2026-10-02", {"a/b.jpg": b"12", "c.jpg": b"1"})
    legacy = state.config.photos_dir / ".trash" / "old-lib" / "2026-10-01"
    legacy.mkdir(parents=True)
    (legacy / DAY_MARKER).touch()
    listing = client.get("/api/trash").json()
    assert listing == [
        {
            "library_id": library_id,
            "display_name": "旭刚",
            "target_dir": lib.relative_to(state.config.photos_dir).as_posix(),
            "days": [{"day": "2026-10-02", "files": 2, "bytes": 3}],
        }
    ]  # every library, no orphan rels
    files = client.get("/api/trash/2026-10-02", params={"library": library_id}).json()
    assert files == [{"rel": "a/b.jpg", "size": 2}, {"rel": "c.jpg", "size": 1}]
    # the old rel parameter is gone
    old = client.get("/api/trash/2026-10-02", params={"rel": "旭刚"})
    assert old.status_code in (400, 422)
    assert _delete_rel(client).status_code in (400, 422)
    assert client.get("/api/trash/2026-10-02", params={"library": "nope"}).status_code == 404


def _delete_rel(client: TestClient) -> Response:
    return client.request("DELETE", "/api/trash/2026-10-02", params={"rel": "旭刚"}, json={})


def test_trash_day_delete_and_keep(api: API) -> None:
    client, state = api
    _, library_id = onboard(client)
    lib = _lib_dir(client, state, library_id)
    day_dir = _seed(lib, "2026-10-01", {"a/x.jpg": b"1", "y.jpg": b"22"})
    params = {"library": library_id}

    body = {"paths": ["y.jpg", "../z"]}
    bad = client.post("/api/trash/2026-10-01/keep", params=params, json=body)
    assert bad.status_code == 400
    assert (day_dir / "y.jpg").exists()

    kept = client.post("/api/trash/2026-10-01/keep", params=params, json={"paths": ["a/x.jpg"]})
    assert kept.status_code == 200
    assert kept.json() == {"kept": ["a/x.jpg"]}
    assert (lib / ".icloud2nas" / "kept" / "a" / "x.jpg").read_bytes() == b"1"
    assert not (state.config.photos_dir / "kept").exists()

    assert _delete(client, "2026-13-01", library_id).status_code == 400
    deleted = _delete(client, "2026-10-01", library_id)
    assert deleted.json() == {"deleted": 1}
    assert not day_dir.exists()
    assert client.get("/api/trash/2026-10-01", params=params).status_code == 400


def test_trash_day_of_a_running_library_is_busy(
    api: API, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, state = api
    _, library_id = onboard(client)
    day_dir = _seed(_lib_dir(client, state, library_id), "2026-10-01", {"y.jpg": b"1"})
    set_scenario(monkeypatch, {"sync": {"events": [{"ev": "started"}, {"sleep": 2}], "exit": 0}})
    assert client.post(f"/api/libraries/{library_id}/sync", json={}).status_code == 202
    client.portal.call(state.scheduler.tick)  # type: ignore[union-attr]
    wait_for(client, lambda c: library_id in state.scheduler.running_libraries)
    busy = _delete(client, "2026-10-01", library_id)
    assert busy.status_code == 409 and busy.json()["error"] == "busy"
    keep = client.post(
        "/api/trash/2026-10-01/keep", params={"library": library_id}, json={"paths": ["y.jpg"]}
    )
    assert keep.status_code == 409
    assert (day_dir / "y.jpg").exists()
    wait_for(client, lambda c: not state.scheduler.running_libraries, tries=200)
    assert _delete(client, "2026-10-01", library_id).json() == {"deleted": 1}


def test_keep_hands_created_kept_folders_to_the_owner(
    api: API, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, state = api
    account_id, library_id = onboard(client)
    wait_for(client, lambda c: not state.scheduler.account_busy(account_id))
    lib = _lib_dir(client, state, library_id)
    _seed(lib, "2026-10-02", {"a/b.jpg": b"12", "c.jpg": b"1"})
    with state.store.session() as s:
        account = repo.get_account(s, account_id)
        assert account is not None
        account.owner_uid, account.owner_gid = 1000, 10
    calls: list[tuple[Path, int, int]] = []
    monkeypatch.setattr(owners, "privileged", lambda: True)
    monkeypatch.setattr(
        owners.os, "fchown",
        lambda fd, u, g: calls.append((Path(os.readlink(f"/proc/self/fd/{fd}")), u, g)),
    )
    response = client.post(
        "/api/trash/2026-10-02/keep", params={"library": library_id},
        json={"paths": ["a/b.jpg", "c.jpg"]},
    )
    assert response.status_code == 200, response.text
    kept = lib / ".icloud2nas" / "kept"
    assert calls == [(kept, 1000, 10), (kept / "a", 1000, 10)]


def test_a_library_behind_a_symlinked_parent_is_refused_with_400(api: API) -> None:
    client, state = api
    account_id, library_id = onboard(client)
    wait_for(client, lambda c: not state.scheduler.account_busy(account_id))
    photos = state.config.photos_dir
    victim = _seed(photos / "Ann" / "iCloud", "2026-10-02", {"a.jpg": b"ann"})
    (photos / "Eve").symlink_to(photos / "Ann")
    with state.store.session() as s:
        library = repo.get_library(s, library_id)
        assert library is not None
        library.target_dir = "Eve/iCloud"  # Eve's library path, parent swapped for a link
    for response in (
        client.request("DELETE", "/api/trash/2026-10-02", params={"library": library_id}, json={}),
        client.get("/api/trash/2026-10-02", params={"library": library_id}),
        client.post("/api/trash/2026-10-02/keep", params={"library": library_id},
                    json={"paths": ["a.jpg"]}),
    ):
        assert response.status_code == 400, response.text
        assert "symlink" in response.json()["detail"]
    listing = client.get("/api/trash")
    assert listing.status_code == 200
    assert next(e for e in listing.json() if e["library_id"] == library_id)["days"] == []
    assert (victim / "a.jpg").read_bytes() == b"ann"
