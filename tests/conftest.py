import sys
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
import tzlocal

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
        }
    )
    cfg.ensure_layout()
    (tmp_path / "photos").mkdir(exist_ok=True)
    return cfg
