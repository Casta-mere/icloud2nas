import io
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
import tzlocal
from fastapi.testclient import TestClient

from icloud2nas import ctl
from icloud2nas.api.state import AppState, build_state
from icloud2nas.main import create_app
from icloud2nas.orchestrator.config import Config

FAKE_WORKER = Path(__file__).resolve().parent / "fake_worker.py"


@pytest.fixture(autouse=True)
def shanghai_tz(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """icloudpd derives year/month folders from the local zone; pin it for determinism."""
    monkeypatch.setenv("TZ", "Asia/Shanghai")
    time.tzset()
    tzlocal.reload_localzone()
    yield
    tzlocal.reload_localzone()


@pytest.fixture
def photos_tree(tmp_path: Path) -> tuple[Path, Path]:
    """(photos_root, library_dir) with library_dir = photos_root/旭刚 created empty."""
    photos = tmp_path / "photos"
    lib = photos / "旭刚"
    lib.mkdir(parents=True)
    return photos, lib


@pytest.fixture
def config(tmp_path: Path) -> Config:
    cfg = Config.from_env(
        {
            "I2N_DATA_DIR": str(tmp_path / "data"),
            "I2N_PHOTOS_DIR": str(tmp_path / "photos"),
            "I2N_WORKER_CMD": f"{sys.executable} {FAKE_WORKER}",
            "TZ": "Asia/Shanghai",
            "I2N_WEB_DIST": str(tmp_path / "no-dist"),  # never pick up a real web/dist build
        }
    )
    cfg.ensure_layout()
    (tmp_path / "photos").mkdir(exist_ok=True)
    return cfg


def make_env(config: Config) -> dict[str, str]:
    return {
        "I2N_DATA_DIR": str(config.data_dir),
        "I2N_PHOTOS_DIR": str(config.photos_dir),
        "I2N_WORKER_CMD": " ".join(config.worker_cmd),
        "TZ": "Asia/Shanghai",
        "I2N_JITTER_S": "0,0",
    }


@pytest.fixture
def sent() -> list[tuple[list[str], str, str]]:
    return []


@pytest.fixture
def api(
    config: Config, sent: list[tuple[list[str], str, str]]
) -> Iterator[tuple[TestClient, AppState]]:
    """The FastAPI app with its lifespan running and the fake worker configured."""

    def recording_sender(urls: list[str], title: str, body: str) -> bool:
        sent.append((urls, title, body))
        return True

    env = make_env(config)
    state = build_state(config, env, send=recording_sender)
    app = create_app(config, env, state=state)
    with TestClient(app) as client:
        yield client, state


def ctl_run(
    client: TestClient,
    argv: list[str],
    codes: list[str] | None = None,
    password: str | None = None,
    env: dict[str, str] | None = None,
) -> tuple[int, str]:
    """Run icloud2nas-ctl against the TestClient; returns (exit code, output)."""
    out = io.StringIO()
    queued = list(codes or [])
    rc = ctl.main(
        argv,
        env={"I2N_URL": "http://testserver", **(env or {})},
        ask_code=lambda prompt: queued.pop(0),
        ask_password=lambda prompt: password or "",
        out=out,
        client=client,
    )
    return rc, out.getvalue()
