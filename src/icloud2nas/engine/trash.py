"""Trash-first deletion: library files are renamed under <photos_root>/.trash, never unlinked."""

from __future__ import annotations

import errno
import os
import shutil
from datetime import date, timedelta
from pathlib import Path

TRASH_DIRNAME = ".trash"
KEPT_DIRNAME = "kept"
DAY_MARKER = ".i2n-day"


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


def _is_day_dir(path: Path) -> bool:
    """A trash day dir is ISO-date-named AND carries the marker move_to_trash writes."""
    try:
        date.fromisoformat(path.name)
    except ValueError:
        return False
    return (path / DAY_MARKER).is_file()


def _copy_then_unlink(src: Path, dest: Path) -> None:
    """Cross-device fallback: copy to a temp name, move it into place, then drop the source."""
    tmp = dest.with_name(dest.name + ".part")
    try:
        shutil.copy2(src, tmp)
        os.replace(tmp, dest)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    os.unlink(src)


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
    day_dir = trash_day_dir(photos_root, library_rel, day or date.today())
    day_dir.mkdir(parents=True, exist_ok=True)
    (day_dir / DAY_MARKER).touch(exist_ok=True)
    dest = _unique(day_dir / rel)
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.rename(src, dest)
    except OSError as exc:
        if exc.errno != errno.EXDEV:
            raise
        _copy_then_unlink(src, dest)
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
    for dirpath, dirnames, _filenames in os.walk(root):
        keep: list[str] = []
        for name in sorted(dirnames):
            candidate = Path(dirpath) / name
            if _is_day_dir(candidate):
                if date.fromisoformat(name) < cutoff:
                    shutil.rmtree(candidate)
                    removed.append(candidate)
                continue  # a day dir's contents are user paths: never descend
            keep.append(name)
        dirnames[:] = keep
    return removed
