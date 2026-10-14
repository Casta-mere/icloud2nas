import time
from collections.abc import Iterator
from pathlib import Path

import pytest
import tzlocal


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
