"""Single-owner lock for worker-spawning operations (daemon vs CLI), based on flock."""

from __future__ import annotations

import fcntl
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


class LockHeld(RuntimeError):
    pass


class DaemonLock:
    """Exclusive flock on <data_dir>/daemon.lock; released automatically when the process dies."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._fd: int | None = None

    def acquire(self, owner: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            holder = self.path.read_text(encoding="utf-8").strip() or "another process"
            raise LockHeld(f"{holder} holds the worker lock; stop it first") from None
        os.ftruncate(fd, 0)
        os.write(fd, f"{owner} pid {os.getpid()}".encode())
        self._fd = fd

    def release(self) -> None:
        if self._fd is not None:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
            os.close(self._fd)
            self._fd = None

    @contextmanager
    def held(self, owner: str) -> Iterator[DaemonLock]:
        self.acquire(owner)
        try:
            yield self
        finally:
            self.release()
