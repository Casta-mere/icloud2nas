from __future__ import annotations

from pathlib import Path

import pytest

from icloud2nas.orchestrator.lock import DaemonLock, LockHeld


def test_lock_is_exclusive_and_released(tmp_path: Path) -> None:
    path = tmp_path / "data" / "daemon.lock"
    first = DaemonLock(path)
    first.acquire("daemon")
    second = DaemonLock(path)
    with pytest.raises(LockHeld, match="daemon pid"):
        second.acquire("icloud2nas-ctl run")
    first.release()
    with second.held("icloud2nas-ctl run"):
        assert "icloud2nas-ctl run" in path.read_text(encoding="utf-8")
    first.acquire("daemon")  # released by the context manager
    first.release()
