"""Trash-first deletion: library files are renamed under <photos_root>/.trash, never unlinked."""

from __future__ import annotations

import errno
import os
import shutil
from datetime import date, timedelta
from pathlib import Path

TRASH_DIRNAME = ".trash"
KEPT_DIRNAME = "kept"


def trash_root(photos_root: str | Path) -> Path:
    return Path(photos_root) / TRASH_DIRNAME


def trash_day_dir(photos_root: str | Path, library_rel: str, day: date) -> Path:
    return trash_root(photos_root) / library_rel / day.isoformat()


def _unique(dest: Path) -> Path:
    if not dest.exists():
        return dest
    n = 1
    while True:
        candidate = dest.with_name(f"{dest.stem}~{n}{dest.suffix}")
        if not candidate.exists():
            return candidate
        n += 1


def move_to_trash(
    photos_root: str | Path,
    library_rel: str,
    library_dir: str | Path,
    abs_path: str | Path,
    day: date | None = None,
) -> Path:
    """Move file to day's trash folder, preserving its relative path within the library."""
    src = Path(abs_path)
    rel = src.resolve().relative_to(Path(library_dir).resolve())  # ValueError if outside
    dest = _unique(trash_day_dir(photos_root, library_rel, day or date.today()) / rel)
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.rename(src, dest)
    except OSError as exc:
        if exc.errno != errno.EXDEV:
            raise
        shutil.copy2(src, dest)
        os.unlink(src)
    return dest


def purge_expired(
    photos_root: str | Path, retention_days: int, today: date | None = None
) -> list[Path]:
    """Delete trash day-folders older than retention_days. 0 disables purging."""
    if retention_days <= 0:
        return []
    cutoff = (today or date.today()) - timedelta(days=retention_days)
    root = trash_root(photos_root)
    removed: list[Path] = []
    if not root.exists():
        return removed
    for candidate in sorted(root.rglob("*")):
        if not candidate.is_dir():
            continue
        try:
            day = date.fromisoformat(candidate.name)
        except ValueError:
            continue
        if day < cutoff:
            shutil.rmtree(candidate)
            removed.append(candidate)
    return removed
