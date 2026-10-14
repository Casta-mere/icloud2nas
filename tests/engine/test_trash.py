import errno
import os
from datetime import date
from pathlib import Path

import pytest

from icloud2nas.engine import trash
from icloud2nas.engine.trash import move_to_trash, purge_expired, trash_day_dir


def _library(tmp_path: Path) -> tuple[Path, Path]:
    photos = tmp_path / "photos"
    lib = photos / "旭刚"
    (lib / "2024" / "05").mkdir(parents=True)
    (lib / "2024" / "05" / "IMG_1.HEIC").write_bytes(b"abc")
    return photos, lib


def test_move_preserves_relative_path_under_day_dir(tmp_path: Path) -> None:
    photos, lib = _library(tmp_path)
    src = lib / "2024" / "05" / "IMG_1.HEIC"
    dest = move_to_trash(photos, "旭刚", lib, src, date(2026, 10, 14))
    assert dest == photos / ".trash" / "旭刚" / "2026-10-14" / "2024" / "05" / "IMG_1.HEIC"
    assert dest.read_bytes() == b"abc" and not src.exists()


def test_move_never_overwrites_same_day(tmp_path: Path) -> None:
    photos, lib = _library(tmp_path)
    src = lib / "2024" / "05" / "IMG_1.HEIC"
    first = move_to_trash(photos, "旭刚", lib, src, date(2026, 10, 14))
    src.write_bytes(b"second")
    second = move_to_trash(photos, "旭刚", lib, src, date(2026, 10, 14))
    assert first.read_bytes() == b"abc"
    assert second.name == "IMG_1~1.HEIC" and second.read_bytes() == b"second"


def test_move_refuses_files_outside_library(tmp_path: Path) -> None:
    photos, lib = _library(tmp_path)
    outsider = tmp_path / "other.txt"
    outsider.write_text("x")
    with pytest.raises(ValueError):
        move_to_trash(photos, "旭刚", lib, outsider)


def test_move_falls_back_to_copy_across_devices(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    photos, lib = _library(tmp_path)
    src = lib / "2024" / "05" / "IMG_1.HEIC"
    real_rename = os.rename

    def exdev(a: str | Path, b: str | Path) -> None:
        raise OSError(errno.EXDEV, "cross-device")

    monkeypatch.setattr(trash.os, "rename", exdev)
    dest = move_to_trash(photos, "旭刚", lib, src, date(2026, 10, 14))
    monkeypatch.setattr(trash.os, "rename", real_rename)
    assert dest.read_bytes() == b"abc" and not src.exists()


def test_purge_removes_only_expired_day_dirs(tmp_path: Path) -> None:
    photos = tmp_path / "photos"
    old = trash_day_dir(photos, "旭刚", date(2026, 9, 1))
    fresh = trash_day_dir(photos, "shared/家庭", date(2026, 10, 10))
    for d in (old, fresh):
        (d / "2024").mkdir(parents=True)
        (d / "2024" / "f.HEIC").write_bytes(b"x")
        (d / trash.DAY_MARKER).touch()
    removed = purge_expired(photos, 30, today=date(2026, 10, 14))
    assert removed == [old]
    assert not old.exists() and fresh.exists()


def test_purge_zero_means_never(tmp_path: Path) -> None:
    photos = tmp_path / "photos"
    old = trash_day_dir(photos, "旭刚", date(2020, 1, 1))
    old.mkdir(parents=True)
    assert purge_expired(photos, 0, today=date(2026, 10, 14)) == []
    assert old.exists()


def test_purge_ignores_date_names_without_marker_and_never_descends(tmp_path: Path) -> None:
    photos = tmp_path / "photos"
    lib = photos / "2024-05-01"  # a library whose display name looks like a date
    (lib / "x").mkdir(parents=True)
    (lib / "x" / "f.HEIC").write_bytes(b"x")
    fresh = move_to_trash(photos, "2024-05-01", lib, lib / "x" / "f.HEIC", date(2026, 10, 10))
    # a date-named folder *inside* a fresh day dir (folder_structure "{:%Y-%m-%d}")
    day = trash_day_dir(photos, "旭刚", date(2026, 10, 12))
    nested = day / "2020-01-01"
    nested.mkdir(parents=True)
    (day / trash.DAY_MARKER).touch()
    (nested / "old.HEIC").write_bytes(b"x")
    assert purge_expired(photos, 30, today=date(2026, 10, 14)) == []
    assert fresh.exists() and (nested / "old.HEIC").exists()


def test_cross_device_copy_failure_leaves_source_and_no_partial(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    photos, lib = _library(tmp_path)
    src = lib / "2024" / "05" / "IMG_1.HEIC"

    def exdev(a: str | Path, b: str | Path) -> None:
        raise OSError(errno.EXDEV, "cross-device")

    def broken_copy(a: str | Path, b: str | Path) -> None:
        Path(b).write_bytes(b"ab")
        raise OSError(errno.ENOSPC, "disk full")

    monkeypatch.setattr(trash.os, "rename", exdev)
    monkeypatch.setattr(trash.shutil, "copy2", broken_copy)
    with pytest.raises(OSError):
        move_to_trash(photos, "旭刚", lib, src, date(2026, 10, 14))
    assert src.read_bytes() == b"abc"
    day = trash_day_dir(photos, "旭刚", date(2026, 10, 14))
    assert not list(day.rglob("*.part")) and not list(day.rglob("IMG_1*"))
