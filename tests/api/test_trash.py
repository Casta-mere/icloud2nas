from __future__ import annotations

from fastapi.testclient import TestClient

from icloud2nas.api.state import AppState
from icloud2nas.engine.trash import DAY_MARKER
from tests.api.conftest import onboard

API = tuple[TestClient, AppState]


def _seed(state: AppState, rel: str, day: str, files: dict[str, bytes]) -> None:
    day_dir = state.config.photos_dir / ".trash" / rel / day
    day_dir.mkdir(parents=True, exist_ok=True)
    (day_dir / DAY_MARKER).touch()
    for name, data in files.items():
        path = day_dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


def test_trash_listing_includes_orphans(api: API) -> None:
    client, state = api
    _, library_id = onboard(client)
    target = client.get("/api/libraries").json()[0]["target_dir"]
    _seed(state, "old-lib", "2026-10-01", {"x.jpg": b"abc"})
    _seed(state, target, "2026-10-02", {"a/b.jpg": b"12", "c.jpg": b"1"})
    listing = client.get("/api/trash").json()
    assert [row["rel"] for row in listing] == [target, "old-lib"]
    current, orphan = listing
    assert current["library_id"] == library_id
    assert current["display_name"] == "旭刚"
    assert current["days"] == [{"day": "2026-10-02", "files": 2, "bytes": 3}]
    assert orphan["library_id"] is None
    assert orphan["display_name"] is None
    assert orphan["days"] == [{"day": "2026-10-01", "files": 1, "bytes": 3}]


def test_trash_day_delete_and_keep(api: API) -> None:
    client, state = api
    _seed(state, "旭刚", "2026-10-01", {"a/x.jpg": b"1", "y.jpg": b"22"})
    files = client.get("/api/trash/2026-10-01", params={"rel": "旭刚"}).json()
    assert files == [{"rel": "a/x.jpg", "size": 1}, {"rel": "y.jpg", "size": 2}]

    bad = client.post(
        "/api/trash/2026-10-01/keep", params={"rel": "旭刚"}, json={"paths": ["y.jpg", "../z"]}
    )
    assert bad.status_code == 400
    assert (state.config.photos_dir / ".trash/旭刚/2026-10-01/y.jpg").exists()

    kept = client.post(
        "/api/trash/2026-10-01/keep", params={"rel": "旭刚"}, json={"paths": ["a/x.jpg"]}
    )
    assert kept.status_code == 200
    assert kept.json() == {"kept": ["a/x.jpg"]}
    assert (state.config.photos_dir / "kept/旭刚/a/x.jpg").read_bytes() == b"1"

    assert client.delete("/api/trash/2026-13-01", params={"rel": "旭刚"}).status_code == 400
    assert client.delete("/api/trash/2026-10-01", params={"rel": ".."}).status_code == 400
    deleted = client.delete("/api/trash/2026-10-01", params={"rel": "旭刚"})
    assert deleted.json() == {"deleted": 1}
    assert not (state.config.photos_dir / ".trash/旭刚/2026-10-01").exists()
    assert client.get("/api/trash/2026-10-01", params={"rel": "旭刚"}).status_code == 400


def test_trash_rel_validated_at_route(api: API) -> None:
    client, state = api
    _seed(state, "旭刚", "2026-10-01", {"y.jpg": b"1"})
    _seed(state, ".hidden", "2026-10-01", {"y.jpg": b"1"})
    for rel in (".", ".hidden", "a/.b", "旭刚/", "/abs", " "):
        params = {"rel": rel}
        assert client.get("/api/trash/2026-10-01", params=params).status_code == 400, rel
        assert client.delete("/api/trash/2026-10-01", params=params).status_code == 400, rel
        keep = client.post("/api/trash/2026-10-01/keep", params=params, json={"paths": []})
        assert keep.status_code == 400, rel
    assert (state.config.photos_dir / ".trash/.hidden/2026-10-01/y.jpg").exists()
    assert (state.config.photos_dir / ".trash/旭刚/2026-10-01/y.jpg").exists()
