from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from icloud2nas.engine.trash import (
    DAY_MARKER,
    delete_day,
    keep_files,
    list_day,
    list_days,
    list_trash_rels,
    trash_bytes,
    trash_directory,
)


def test_trash_directory_moves_whole_library(photos_tree: tuple[Path, Path]) -> None:
    photos, lib = photos_tree
    (lib / "2026" / "10").mkdir(parents=True)
    (lib / "2026" / "10" / "a.jpg").write_bytes(b"x")
    day = date(2026, 10, 21)
    dest = trash_directory(photos, "旭刚", day)
    assert dest == photos / ".trash" / "旭刚" / "2026-10-21" / "旭刚"
    assert dest is not None and (dest / "2026" / "10" / "a.jpg").read_bytes() == b"x"
    assert (photos / ".trash" / "旭刚" / "2026-10-21" / DAY_MARKER).is_file()
    assert not lib.exists()
    assert trash_directory(photos, "旭刚", day) is None


def _day(photos: Path, rel: str, day: str, files: dict[str, bytes]) -> Path:
    d = photos / ".trash" / rel / day
    d.mkdir(parents=True)
    (d / DAY_MARKER).touch()
    for name, data in files.items():
        (d / name).parent.mkdir(parents=True, exist_ok=True)
        (d / name).write_bytes(data)
    return d


def test_list_days_and_day(photos_tree: tuple[Path, Path]) -> None:
    photos, _ = photos_tree
    _day(photos, "旭刚", "2026-10-01", {"2024/05/x.HEIC": b"12345", "y.jpg": b"12"})
    _day(photos, "旭刚", "2026-10-05", {"z.jpg": b"1"})
    days = list_days(photos, "旭刚")
    assert [d.day for d in days] == [date(2026, 10, 5), date(2026, 10, 1)]
    assert (days[1].files, days[1].bytes) == (2, 7)
    files = list_day(photos, "旭刚", date(2026, 10, 1))
    assert [(f.rel, f.size) for f in files] == [("2024/05/x.HEIC", 5), ("y.jpg", 2)]
    assert list_days(photos, "nobody") == []
    assert trash_bytes(photos) == 8


def test_list_trash_rels_nested_and_empty(photos_tree: tuple[Path, Path]) -> None:
    photos, _ = photos_tree
    assert list_trash_rels(photos) == []
    assert trash_bytes(photos) == 0
    _day(photos, "家/旭刚", "2026-10-01", {"a": b"x"})
    _day(photos, "幻伊镜", "2026-10-02", {})
    assert list_trash_rels(photos) == ["家/旭刚", "幻伊镜"]


def test_delete_day_removes_only_that_day(photos_tree: tuple[Path, Path]) -> None:
    photos, _ = photos_tree
    d1 = _day(photos, "旭刚", "2026-10-01", {"a": b"1", "b/c": b"2"})
    d2 = _day(photos, "旭刚", "2026-10-05", {"z": b"1"})
    assert delete_day(photos, "旭刚", date(2026, 10, 1)) == 2
    assert not d1.exists() and (d2 / "z").exists()
    with pytest.raises(ValueError):
        delete_day(photos, "旭刚", date(2026, 10, 1))


def test_keep_files_moves_and_refuses_traversal(photos_tree: tuple[Path, Path]) -> None:
    photos, _ = photos_tree
    d = _day(photos, "旭刚", "2026-10-01", {"2024/05/x.HEIC": b"h", "y.jpg": b"j"})
    (photos / "secret").write_bytes(b"s")
    day = date(2026, 10, 1)
    for bad in ("../../../../secret", "/etc/passwd", DAY_MARKER, "missing.jpg"):
        with pytest.raises(ValueError, match=r"."):
            keep_files(photos, "旭刚", day, ["y.jpg", bad])
    assert (d / "y.jpg").exists() and (d / "2024/05/x.HEIC").exists()
    assert not (photos / "kept").exists()
    out = keep_files(photos, "旭刚", day, ["2024/05/x.HEIC"])
    assert out == [photos / "kept" / "旭刚" / "2024" / "05" / "x.HEIC"]
    assert out[0].read_bytes() == b"h" and not (d / "2024/05/x.HEIC").exists()
    (d / "2024/05").mkdir(exist_ok=True)
    (d / "2024/05/x.HEIC").write_bytes(b"h2")
    again = keep_files(photos, "旭刚", day, ["2024/05/x.HEIC"])
    assert again[0].name == "x~1.HEIC"


def test_library_rel_cannot_escape_trash(photos_tree: tuple[Path, Path]) -> None:
    photos, _ = photos_tree
    day = date(2026, 10, 1)
    outside = photos / "x" / "2026-10-01"
    outside.mkdir(parents=True)
    (outside / DAY_MARKER).touch()
    (photos / ".trash").mkdir()
    for bad in ("../x", "/abs", "", "a/../../x"):
        with pytest.raises(ValueError, match=r"."):
            delete_day(photos, bad, day)
        with pytest.raises(ValueError, match=r"."):
            list_days(photos, bad)
        with pytest.raises(ValueError, match=r"."):
            list_day(photos, bad, day)
        with pytest.raises(ValueError, match=r"."):
            keep_files(photos, bad, day, ["a"])
    assert outside.is_dir() and (outside / DAY_MARKER).exists()


def test_keep_files_refuses_marker_alias_and_duplicates(photos_tree: tuple[Path, Path]) -> None:
    photos, _ = photos_tree
    d = _day(photos, "旭刚", "2026-10-01", {"a": b"1"})
    day = date(2026, 10, 1)
    for bad in (["./" + DAY_MARKER], ["a", "a"], ["a", "./a"]):
        with pytest.raises(ValueError, match=r"."):
            keep_files(photos, "旭刚", day, bad)
    assert (d / "a").exists() and (d / DAY_MARKER).exists() and not (photos / "kept").exists()
