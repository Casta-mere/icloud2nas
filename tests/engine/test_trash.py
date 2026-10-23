import errno
import os
from datetime import date
from pathlib import Path

import pytest

from icloud2nas.engine import trash
from icloud2nas.engine.paths import walk_library
from icloud2nas.engine.reconcile import apply_paths, compute_plan
from icloud2nas.engine.trash import (
    DAY_MARKER,
    keep_files,
    kept_dir,
    move_to_trash,
    purge_expired,
    trash_day_dir,
    trash_dir,
    trash_library_dir,
)


def _library(tmp_path: Path) -> tuple[Path, Path]:
    photos = tmp_path / "photos"
    lib = photos / "小明"
    (lib / "2024" / "05").mkdir(parents=True)
    (lib / "2024" / "05" / "IMG_1.HEIC").write_bytes(b"abc")
    return photos, lib


def test_layout_lives_inside_the_library(tmp_path: Path) -> None:
    lib = tmp_path / "lib"
    assert trash_dir(lib) == lib / ".icloud2nas" / "trash"
    assert kept_dir(lib) == lib / ".icloud2nas" / "kept"
    assert trash_day_dir(lib, date(2026, 10, 14)) == lib / ".icloud2nas" / "trash" / "2026-10-14"


def test_move_to_trash_goes_inside_the_library(tmp_path: Path) -> None:
    photos, lib = _library(tmp_path)
    src = lib / "2024" / "05" / "IMG_1.HEIC"
    dest = move_to_trash(lib, src, date(2026, 10, 14))
    assert dest == lib / ".icloud2nas" / "trash" / "2026-10-14" / "2024" / "05" / "IMG_1.HEIC"
    assert dest.read_bytes() == b"abc" and not src.exists()
    assert (lib / ".icloud2nas" / "trash" / "2026-10-14" / DAY_MARKER).is_file()
    assert sorted(p.name for p in photos.iterdir()) == ["小明"]  # nothing at the mount root


def test_move_never_overwrites_same_day(tmp_path: Path) -> None:
    _, lib = _library(tmp_path)
    src = lib / "2024" / "05" / "IMG_1.HEIC"
    first = move_to_trash(lib, src, date(2026, 10, 14))
    src.write_bytes(b"second")
    second = move_to_trash(lib, src, date(2026, 10, 14))
    assert first.read_bytes() == b"abc"
    assert second.name == "IMG_1~1.HEIC" and second.read_bytes() == b"second"


def test_move_refuses_files_outside_library_or_in_the_store(tmp_path: Path) -> None:
    _, lib = _library(tmp_path)
    outsider = tmp_path / "other.txt"
    outsider.write_text("x")
    with pytest.raises(ValueError):
        move_to_trash(lib, outsider)
    inside_store = move_to_trash(lib, lib / "2024" / "05" / "IMG_1.HEIC", date(2026, 10, 14))
    with pytest.raises(ValueError):
        move_to_trash(lib, inside_store)
    with pytest.raises(ValueError):
        move_to_trash("relative/lib", "relative/lib/x")


def test_move_falls_back_to_copy_across_devices(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, lib = _library(tmp_path)
    src = lib / "2024" / "05" / "IMG_1.HEIC"

    def exdev(a: str | Path, b: str | Path) -> None:
        raise OSError(errno.EXDEV, "cross-device")

    monkeypatch.setattr(trash.os, "rename", exdev)
    dest = move_to_trash(lib, src, date(2026, 10, 14))
    monkeypatch.undo()
    assert dest.read_bytes() == b"abc" and not src.exists()


def test_cross_device_copy_failure_leaves_source_and_no_partial(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, lib = _library(tmp_path)
    src = lib / "2024" / "05" / "IMG_1.HEIC"

    def exdev(a: str | Path, b: str | Path) -> None:
        raise OSError(errno.EXDEV, "cross-device")

    def broken_copy(a: str | Path, b: str | Path) -> None:
        Path(b).write_bytes(b"ab")
        raise OSError(errno.ENOSPC, "disk full")

    monkeypatch.setattr(trash.os, "rename", exdev)
    monkeypatch.setattr(trash.shutil, "copy2", broken_copy)
    with pytest.raises(OSError):
        move_to_trash(lib, src, date(2026, 10, 14))
    assert src.read_bytes() == b"abc"
    day = trash_day_dir(lib, date(2026, 10, 14))
    assert not list(day.rglob("*.part")) and not list(day.rglob("IMG_1*"))


def test_walk_library_ignores_icloud2nas(tmp_path: Path) -> None:
    _, lib = _library(tmp_path)
    kept = kept_dir(lib) / "2024" / "k.HEIC"
    kept.parent.mkdir(parents=True)
    kept.write_bytes(b"k")
    move_to_trash(lib, lib / "2024" / "05" / "IMG_1.HEIC", date(2026, 10, 14))
    (lib / "2024" / "05" / "IMG_2.HEIC").write_bytes(b"2")
    assert walk_library(lib) == {os.path.normpath(lib / "2024" / "05" / "IMG_2.HEIC")}


def test_keep_moves_into_kept_and_reconcile_ignores_it(tmp_path: Path) -> None:
    _, lib = _library(tmp_path)
    day = date(2026, 10, 14)
    move_to_trash(lib, lib / "2024" / "05" / "IMG_1.HEIC", day)
    out = keep_files(lib, day, ["2024/05/IMG_1.HEIC"], photos=lib.parent)
    assert out == [lib / ".icloud2nas" / "kept" / "2024" / "05" / "IMG_1.HEIC"]
    assert out[0].read_bytes() == b"abc"
    local = walk_library(lib)
    assert local == set()
    plan = compute_plan(set(), local, str(lib), remote_total=0)
    assert plan.extraneous == []  # kept files are never proposed for trashing again


def test_apply_paths_trashes_into_the_library(tmp_path: Path) -> None:
    _, lib = _library(tmp_path)
    moved = apply_paths(["2024/05/IMG_1.HEIC", "gone.jpg"], str(lib), date(2026, 10, 14))
    assert moved == 1
    assert (trash_day_dir(lib, date(2026, 10, 14)) / "2024" / "05" / "IMG_1.HEIC").exists()


def _seed_day(lib: Path, day: str, files: dict[str, bytes], *, marker: bool = True) -> Path:
    d = lib / ".icloud2nas" / "trash" / day
    d.mkdir(parents=True)
    if marker:
        (d / DAY_MARKER).touch()
    for name, data in files.items():
        (d / name).parent.mkdir(parents=True, exist_ok=True)
        (d / name).write_bytes(data)
    return d


def test_purge_removes_only_expired_day_dirs(tmp_path: Path) -> None:
    _, lib = _library(tmp_path)
    old = _seed_day(lib, "2026-09-01", {"2024/f.HEIC": b"x"})
    fresh = _seed_day(lib, "2026-10-10", {"2024/f.HEIC": b"x"})
    unmarked = _seed_day(lib, "2020-01-01", {"f": b"x"}, marker=False)
    assert purge_expired(lib, 30, today=date(2026, 10, 14), photos=lib.parent) == [old]
    assert not old.exists() and fresh.exists() and unmarked.exists()
    assert (lib / "2024" / "05" / "IMG_1.HEIC").exists()


def test_purge_zero_means_never_and_missing_trash_is_empty(tmp_path: Path) -> None:
    _, lib = _library(tmp_path)
    old = _seed_day(lib, "2020-01-01", {})
    assert purge_expired(lib, 0, today=date(2026, 10, 14), photos=lib.parent) == []
    assert old.exists()
    assert purge_expired(tmp_path / "absent", 30, today=date(2026, 10, 14), photos=tmp_path) == []


def test_purge_never_descends_into_a_day(tmp_path: Path) -> None:
    _, lib = _library(tmp_path)
    day = _seed_day(lib, "2026-10-12", {"2020-01-01/old.HEIC": b"x"})
    (day / "2020-01-01" / DAY_MARKER).touch()  # looks like a day dir, but is user content
    assert purge_expired(lib, 30, today=date(2026, 10, 14), photos=lib.parent) == []
    assert (day / "2020-01-01" / "old.HEIC").exists()


def test_trash_library_dir_renames_to_sibling_deleted_dir(tmp_path: Path) -> None:
    photos, lib = _library(tmp_path)
    today = date(2026, 10, 23)
    dest = trash_library_dir(lib, today, photos=lib.parent)
    assert dest == photos / ".icloud2nas-deleted" / "小明-2026-10-23"
    assert (dest / "2024" / "05" / "IMG_1.HEIC").read_bytes() == b"abc"
    assert not lib.exists()
    assert trash_library_dir(lib, today, photos=lib.parent) is None
    lib.mkdir()
    again = trash_library_dir(lib, today, photos=lib.parent)
    assert again is not None and again != dest and again.parent == dest.parent
    assert again.name.startswith("小明-2026-10-23")


@pytest.mark.parametrize("err", [errno.EXDEV, errno.EBUSY])
def test_trash_library_dir_never_copies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, err: int
) -> None:
    photos, lib = _library(tmp_path)

    def refuse(*_: object, **__: object) -> None:
        raise OSError(err, os.strerror(err))

    monkeypatch.setattr(trash.os, "rename", refuse)
    with pytest.raises(ValueError):
        trash_library_dir(lib, date(2026, 10, 23), photos=lib.parent)
    monkeypatch.undo()
    assert (lib / "2024" / "05" / "IMG_1.HEIC").exists()
    assert not (photos / ".icloud2nas-deleted").exists()


def test_trash_library_dir_refuses_a_mount_point(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, lib = _library(tmp_path)
    monkeypatch.setattr(trash.os.path, "ismount", lambda p: Path(p) == lib)
    with pytest.raises(ValueError, match="mount"):
        trash_library_dir(lib, date(2026, 10, 23), photos=lib.parent)
    assert lib.is_dir()


def test_remove_deleted_dir_stays_inside_the_photos_root(tmp_path: Path) -> None:
    photos = tmp_path / "photos"
    inside = photos / "Ann" / trash.DELETED_DIRNAME / "lib-2026-01-01"
    (inside / "2024").mkdir(parents=True)
    outside = tmp_path / "elsewhere" / trash.DELETED_DIRNAME / "lib-2026-01-01"
    (outside / "2024").mkdir(parents=True)
    (outside / "2024" / "a.jpg").write_bytes(b"a")
    with pytest.raises(ValueError):
        trash.remove_deleted_dir(outside, photos)  # an absolute row outside the root
    assert (outside / "2024" / "a.jpg").exists()
    assert trash.remove_deleted_dir(inside, photos) is True
    assert not inside.exists() and not inside.parent.exists()


def test_remove_deleted_dir_refuses_a_symlinked_deleted_parent(tmp_path: Path) -> None:
    photos = tmp_path / "photos"
    real = tmp_path / "precious"
    (real / "lib-2026-01-01" / "2024").mkdir(parents=True)
    (real / "lib-2026-01-01" / "2024" / "a.jpg").write_bytes(b"a")
    (photos / "Ann").mkdir(parents=True)
    (photos / "Ann" / trash.DELETED_DIRNAME).symlink_to(real)
    target = photos / "Ann" / trash.DELETED_DIRNAME / "lib-2026-01-01"
    with pytest.raises(ValueError):
        trash.remove_deleted_dir(target, photos)
    assert (real / "lib-2026-01-01" / "2024" / "a.jpg").read_bytes() == b"a"
    inside_link = photos / "Bob" / trash.DELETED_DIRNAME
    inside_link.parent.mkdir()
    (photos / "Bob-real").mkdir()
    (photos / "Bob-real" / "lib-2026-01-01").mkdir()
    inside_link.symlink_to(photos / "Bob-real")  # resolves inside the root, still refused
    with pytest.raises(ValueError):
        trash.remove_deleted_dir(inside_link / "lib-2026-01-01", photos)
    assert (photos / "Bob-real" / "lib-2026-01-01").is_dir()


def test_remove_deleted_dir_never_follows_a_swapped_ancestor(tmp_path: Path) -> None:
    photos = tmp_path / "photos"
    real = photos / "Ann-real"
    (real / trash.DELETED_DIRNAME / "lib-2026-01-01" / "2024").mkdir(parents=True)
    (real / trash.DELETED_DIRNAME / "lib-2026-01-01" / "2024" / "a.jpg").write_bytes(b"a")
    (photos / "Ann").symlink_to(real)  # a user swapped their home folder for a link
    target = photos / "Ann" / trash.DELETED_DIRNAME / "lib-2026-01-01"
    with pytest.raises(ValueError):
        trash.remove_deleted_dir(target, photos)
    assert (real / trash.DELETED_DIRNAME / "lib-2026-01-01" / "2024" / "a.jpg").exists()


def test_remove_deleted_dir_drops_a_planted_link_and_reports_missing(tmp_path: Path) -> None:
    photos = tmp_path / "photos"
    parent = photos / "Ann" / trash.DELETED_DIRNAME
    parent.mkdir(parents=True)
    precious = tmp_path / "precious"
    precious.mkdir()
    (precious / "a.jpg").write_bytes(b"a")
    (parent / "lib-2026-01-01").symlink_to(precious)
    assert trash.remove_deleted_dir(parent / "lib-2026-01-01", photos) is True
    assert (precious / "a.jpg").exists()
    assert trash.remove_deleted_dir(parent / "lib-2026-01-02", photos) is False
    assert trash.remove_deleted_dir(photos / "Bob" / trash.DELETED_DIRNAME / "x", photos) is False
