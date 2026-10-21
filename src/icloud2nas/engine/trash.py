"""Trash-first deletion: library files are renamed under <photos_root>/.trash, never unlinked."""

from __future__ import annotations

import errno
import os
import shutil
from dataclasses import dataclass
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


def trash_directory(photos_root: str | Path, library_rel: str, day: date) -> Path | None:
    """Move a whole library folder to <.trash>/<library_rel>/<day>/<name>; None if it is absent."""
    src = Path(photos_root) / library_rel
    if not src.exists():
        return None
    day_dir = trash_day_dir(photos_root, library_rel, day)
    day_dir.mkdir(parents=True, exist_ok=True)
    (day_dir / DAY_MARKER).touch(exist_ok=True)
    dest = _unique(day_dir / Path(library_rel).name)
    try:
        os.rename(src, dest)
    except OSError as exc:
        if exc.errno != errno.EXDEV:
            raise
        shutil.move(src, dest)
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


@dataclass(frozen=True)
class TrashDay:
    day: date
    files: int
    bytes: int


@dataclass(frozen=True)
class TrashFile:
    rel: str
    size: int


def _day_files(day_dir: Path) -> list[TrashFile]:
    out: list[TrashFile] = []
    for path in day_dir.rglob("*"):
        if path.is_symlink() or not path.is_file():
            continue
        rel = path.relative_to(day_dir)
        if rel.as_posix() == DAY_MARKER:
            continue
        out.append(TrashFile(rel.as_posix(), path.stat().st_size))
    return sorted(out, key=lambda f: f.rel)


def _trash_lib_dir(photos_root: str | Path, library_rel: str) -> Path:
    """<.trash>/<library_rel>, refusing empty, absolute, '..' or escaping rels."""
    pure = Path(library_rel)
    if not library_rel or pure.is_absolute() or ".." in pure.parts:
        raise ValueError(f"invalid library path: {library_rel!r}")
    root = trash_root(photos_root)
    lib_dir = root / pure
    if not lib_dir.resolve().is_relative_to(root.resolve()):
        raise ValueError(f"invalid library path: {library_rel!r}")
    return lib_dir


def _require_day_dir(photos_root: str | Path, library_rel: str, day: date) -> Path:
    day_dir = _trash_lib_dir(photos_root, library_rel) / day.isoformat()
    if not _is_day_dir(day_dir):
        raise ValueError(f"no trash day {day.isoformat()} for {library_rel!r}")
    return day_dir


def list_days(photos_root: str | Path, library_rel: str) -> list[TrashDay]:
    """Day folders of one library's trash, newest first."""
    base = _trash_lib_dir(photos_root, library_rel)
    if not base.is_dir():
        return []
    days: list[TrashDay] = []
    for child in base.iterdir():
        if _is_day_dir(child):
            files = _day_files(child)
            days.append(
                TrashDay(date.fromisoformat(child.name), len(files), sum(f.size for f in files))
            )
    return sorted(days, key=lambda d: d.day, reverse=True)


def list_day(photos_root: str | Path, library_rel: str, day: date) -> list[TrashFile]:
    return _day_files(_require_day_dir(photos_root, library_rel, day))


def delete_day(photos_root: str | Path, library_rel: str, day: date) -> int:
    """Permanently delete one trash day folder (inside .trash only); returns files removed."""
    day_dir = _require_day_dir(photos_root, library_rel, day)
    count = len(_day_files(day_dir))
    shutil.rmtree(day_dir)
    return count


def keep_files(
    photos_root: str | Path, library_rel: str, day: date, rels: list[str]
) -> list[Path]:
    """Rescue trash files into <photos_root>/kept/<library_rel>/<rel>; all-or-nothing validation."""
    day_dir = _require_day_dir(photos_root, library_rel, day)
    base = day_dir.resolve()
    checked: list[tuple[str, Path]] = []
    seen: set[Path] = set()
    for rel in rels:
        pure = Path(rel)
        if pure.is_absolute() or ".." in pure.parts:
            raise ValueError(f"invalid trash path: {rel!r}")
        src = (day_dir / rel).resolve()
        if (
            not src.is_relative_to(base)
            or not src.is_file()
            or (src.name == DAY_MARKER and src.parent == base)
            or src in seen
        ):
            raise ValueError(f"invalid or duplicate trash path: {rel!r}")
        seen.add(src)
        checked.append((rel, src))
    kept_base = Path(photos_root) / KEPT_DIRNAME / Path(library_rel)
    moved: list[Path] = []
    for rel, src in checked:
        dest = _unique(kept_base / rel)
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.rename(src, dest)
        except OSError as exc:
            if exc.errno != errno.EXDEV:
                raise
            _copy_then_unlink(src, dest)
        moved.append(dest)
    return moved


def trash_bytes(photos_root: str | Path) -> int:
    root = trash_root(photos_root)
    if not root.exists():
        return 0
    return sum(p.stat().st_size for p in root.rglob("*") if p.is_file() and not p.is_symlink())


def list_trash_rels(photos_root: str | Path) -> list[str]:
    """Library rels (relative to .trash, '/'-separated) that directly contain a day dir."""
    root = trash_root(photos_root)
    rels: list[str] = []
    if not root.exists():
        return rels
    for dirpath, dirnames, _files in os.walk(root):
        here = Path(dirpath)
        if any(_is_day_dir(here / n) for n in dirnames):
            rels.append(here.relative_to(root).as_posix())
        dirnames[:] = [n for n in dirnames if not _is_day_dir(here / n)]
    return sorted(rels)
