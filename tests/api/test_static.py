from __future__ import annotations

import dataclasses
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from icloud2nas.api.state import build_state
from icloud2nas.main import create_app
from icloud2nas.orchestrator.config import Config

from .conftest import make_env

INDEX = "<!doctype html><title>icloud2nas</title>"


@pytest.fixture
def api_with_dist(config: Config, tmp_path: Path) -> Iterator[TestClient]:
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text(INDEX)
    (dist / "assets" / "app.js").write_text("console.log(1)")
    env = {**make_env(config), "I2N_WEB_DIST": str(dist)}
    cfg = dataclasses.replace(config, web_dist=Config.from_env(env).web_dist)
    app = create_app(cfg, env, state=build_state(cfg, env))
    with TestClient(app) as client:
        yield client


def test_web_dist_defaults_and_reads_env() -> None:
    assert Config.from_env({}).web_dist == Path("web/dist")
    assert Config.from_env({"I2N_WEB_DIST": "/x"}).web_dist == Path("/x")


def test_without_dist_root_explains(api: tuple[TestClient, object]) -> None:
    client, _ = api
    response = client.get("/")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert "not built" in response.text
    assert client.get("/libraries").status_code == 404


def test_with_dist_serves_index_and_assets(api_with_dist: TestClient) -> None:
    for path in ("/", "/libraries"):
        response = api_with_dist.get(path)
        assert response.status_code == 200
        assert response.text == INDEX
        assert response.headers["cache-control"] == "no-cache"
    assert api_with_dist.head("/").status_code == 200
    asset = api_with_dist.get("/assets/app.js")
    assert asset.status_code == 200
    assert asset.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert api_with_dist.get("/assets/missing.js").status_code == 404


def test_with_dist_api_and_probes_keep_their_responses(api_with_dist: TestClient) -> None:
    missing = api_with_dist.get("/api/nope")
    assert missing.status_code == 404
    assert missing.json() == {"error": "not found", "detail": "Not Found"}
    assert api_with_dist.get("/healthz").headers["content-type"].startswith("application/json")
    assert "text/html" not in api_with_dist.get("/metrics").headers["content-type"]
    assert api_with_dist.get("/healthzz").status_code == 404


def test_mutating_rules_untouched(api_with_dist: TestClient) -> None:
    assert api_with_dist.post("/api/pause", content="x").status_code == 415
    assert api_with_dist.post("/anything", content="x").status_code == 405
    assert api_with_dist.get("/anything").text == INDEX
