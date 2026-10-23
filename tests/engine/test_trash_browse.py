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
    migrate_legacy,
    trash_bytes,
)


def _day(lib: Path, day: str, files: dict[str, bytes]) -> Path:
    d = lib / ".icloud2nas" / "trash" / day
    d.mkdir(parents=True)
    (d / DAY_MARKER).touch()
    for name, data in files.items():
        (d / name).parent.mkdir(parents=True, exist_ok=True)
        (d / name).write_bytes(data)
    return d


def test_list_days_and_day(photos_tree: tuple[Path, Path]) -> None:
    photos, lib = photos_tree
    _day(lib, "2026-10-01", {"2024/05/x.HEIC": b"12345", "y.jpg": b"12"})
    _day(lib, "2026-10-05", {"z.jpg": b"1"})
    days = list_days(lib, photos=photos)
    assert [d.day for d in days] == [date(2026, 10, 5), date(2026, 10, 1)]
    assert (days[1].files, days[1].bytes) == (2, 7)
    files = list_day(lib, date(2026, 10, 1), photos=photos)
    assert [(f.rel, f.size) for f in files] == [("2024/05/x.HEIC", 5), ("y.jpg", 2)]
    other = photos / "小红"
    assert list_days(other, photos=photos) == []
    _day(other, "2026-10-02", {"a": b"123"})
    assert trash_bytes([lib, other, photos / "absent"], photos=photos) == 11
    assert trash_bytes([], photos=photos) == 0


def test_delete_day_removes_only_that_day(photos_tree: tuple[Path, Path]) -> None:
    _, lib = photos_tree
    d1 = _day(lib, "2026-10-01", {"a": b"1", "b/c": b"2"})
    d2 = _day(lib, "2026-10-05", {"z": b"1"})
    assert delete_day(lib, date(2026, 10, 1), photos=lib.parent) == 2
    assert not d1.exists() and (d2 / "z").exists()
    with pytest.raises(ValueError):
        delete_day(lib, date(2026, 10, 1), photos=lib.parent)


def test_keep_files_moves_and_refuses_traversal(photos_tree: tuple[Path, Path]) -> None:
    photos, lib = photos_tree
    d = _day(lib, "2026-10-01", {"2024/05/x.HEIC": b"h", "y.jpg": b"j"})
    (photos / "secret").write_bytes(b"s")
    day = date(2026, 10, 1)
    for bad in ("../../../../secret", "/etc/passwd", DAY_MARKER, "missing.jpg"):
        with pytest.raises(ValueError, match=r"."):
            keep_files(lib, day, ["y.jpg", bad], photos=lib.parent)
    assert (d / "y.jpg").exists() and (d / "2024/05/x.HEIC").exists()
    assert not (lib / ".icloud2nas" / "kept").exists()
    out = keep_files(lib, day, ["2024/05/x.HEIC"], photos=lib.parent)
    assert out == [lib / ".icloud2nas" / "kept" / "2024" / "05" / "x.HEIC"]
    assert out[0].read_bytes() == b"h" and not (d / "2024/05/x.HEIC").exists()
    (d / "2024/05").mkdir(exist_ok=True)
    (d / "2024/05/x.HEIC").write_bytes(b"h2")
    again = keep_files(lib, day, ["2024/05/x.HEIC"], photos=lib.parent)
    assert again[0].name == "x~1.HEIC" and again[0].read_bytes() == b"h2"
    assert out[0].read_bytes() == b"h"  # the first one is never overwritten


def test_library_dir_is_validated(photos_tree: tuple[Path, Path]) -> None:
    photos, lib = photos_tree
    day = date(2026, 10, 1)
    outside = photos / "x" / "2026-10-01"
    outside.mkdir(parents=True)
    (outside / DAY_MARKER).touch()
    # a store that is a symlink out of the library must not be followed
    (lib / ".icloud2nas").mkdir()
    (lib / ".icloud2nas" / "trash").symlink_to(photos / "x")
    for bad in (lib, Path("relative/lib"), photos / "a" / ".." / "小明"):
        with pytest.raises(ValueError, match=r"."):
            delete_day(bad, day, photos=photos)
        with pytest.raises(ValueError, match=r"."):
            list_day(bad, day, photos=photos)
        with pytest.raises(ValueError, match=r"."):
            keep_files(bad, day, ["a"], photos=photos)
    with pytest.raises(ValueError):
        list_days(Path("relative/lib"), photos=photos)
    assert outside.is_dir() and (outside / DAY_MARKER).exists()


def test_keep_files_refuses_marker_alias_and_duplicates(photos_tree: tuple[Path, Path]) -> None:
    _, lib = photos_tree
    d = _day(lib, "2026-10-01", {"a": b"1"})
    day = date(2026, 10, 1)
    for bad in (["./" + DAY_MARKER], ["a", "a"], ["a", "./a"]):
        with pytest.raises(ValueError, match=r"."):
            keep_files(lib, day, bad, photos=lib.parent)
    assert (d / "a").exists() and (d / DAY_MARKER).exists()
    assert not (lib / ".icloud2nas" / "kept").exists()


def _legacy_day(photos: Path, rel: str, day: str, files: dict[str, bytes]) -> Path:
    d = photos / ".trash" / rel / day
    d.mkdir(parents=True)
    (d / DAY_MARKER).touch()
    for name, data in files.items():
        (d / name).parent.mkdir(parents=True, exist_ok=True)
        (d / name).write_bytes(data)
    return d


def _count(root: Path) -> tuple[int, int]:
    """(regular files that are not markers, day markers) under root."""
    if not root.exists():
        return 0, 0
    files = [p for p in root.rglob("*") if p.is_file()]
    markers = [p for p in files if p.name == DAY_MARKER]
    return len(files) - len(markers), len(markers)


def test_migrate_legacy_moves_root_trash_and_kept_without_loss(
    photos_tree: tuple[Path, Path],
) -> None:
    photos, lib = photos_tree
    shared = photos / "shared" / "家庭"
    shared.mkdir(parents=True)
    _legacy_day(photos, "小明", "2026-10-01", {"2024/a.HEIC": b"a", "b.jpg": b"b"})
    _legacy_day(photos, "小明", "2026-10-02", {"2024/a.HEIC": b"a2"})
    _legacy_day(photos, "shared/家庭", "2026-10-03", {"s.jpg": b"s"})
    _legacy_day(photos, "old-lib", "2026-09-01", {"o.jpg": b"o"})  # an unknown rel
    # a day already present in the new layout: the legacy one merges into it
    _day(lib, "2026-10-02", {"2024/a.HEIC": b"new"})
    (photos / "kept" / "小明" / "2024").mkdir(parents=True)
    (photos / "kept" / "小明" / "2024" / "k.HEIC").write_bytes(b"k")
    (photos / "kept" / "old-lib").mkdir(parents=True)
    (photos / "kept" / "old-lib" / "x.jpg").write_bytes(b"x")
    (lib / ".icloud2nas" / "kept").mkdir(parents=True)
    (lib / ".icloud2nas" / "kept" / "already.jpg").write_bytes(b"z")

    lines = migrate_legacy(photos, ["小明", "shared/家庭", "小红"])
    assert lines  # something was logged

    new_lib_trash = _count(lib / ".icloud2nas" / "trash")
    assert new_lib_trash == (4, 2)  # 2 + 1 + 1 existing files; days 10-01 and 10-02
    assert _count(shared / ".icloud2nas" / "trash") == (1, 1)
    day2 = lib / ".icloud2nas" / "trash" / "2026-10-02" / "2024"
    assert sorted(p.read_bytes() for p in day2.iterdir()) == [b"a2", b"new"]
    assert (lib / ".icloud2nas" / "kept" / "2024" / "k.HEIC").read_bytes() == b"k"
    assert (lib / ".icloud2nas" / "kept" / "already.jpg").read_bytes() == b"z"
    # unknown rels stay exactly where they were; known ones leave no trace behind
    assert (photos / ".trash" / "old-lib" / "2026-09-01" / "o.jpg").read_bytes() == b"o"
    assert (photos / "kept" / "old-lib" / "x.jpg").read_bytes() == b"x"
    assert not (photos / ".trash" / "小明").exists()
    assert not (photos / ".trash" / "shared").exists()
    assert not (photos / "kept" / "小明").exists()
    assert any("old-lib" in line for line in lines)
    # idempotent
    assert migrate_legacy(photos, ["小明", "shared/家庭"]) == [
        line for line in lines if "old-lib" in line
    ]


def test_migrate_legacy_without_legacy_dirs_is_a_no_op(photos_tree: tuple[Path, Path]) -> None:
    photos, lib = photos_tree
    assert migrate_legacy(photos, ["小明"]) == []
    assert not (lib / ".icloud2nas").exists()


def test_migrate_legacy_skips_a_missing_library_folder(photos_tree: tuple[Path, Path]) -> None:
    photos, _ = photos_tree
    _legacy_day(photos, "gone", "2026-10-01", {"a.jpg": b"a"})
    lines = migrate_legacy(photos, ["gone"])
    assert not (photos / "gone").exists()
    assert (photos / ".trash" / "gone" / "2026-10-01" / "a.jpg").exists()
    assert any("gone" in line for line in lines)


def test_migrate_legacy_leaves_cross_device_trees(
    photos_tree: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    import errno
    import os

    from icloud2nas.engine import trash

    photos, lib = photos_tree
    _legacy_day(photos, "小明", "2026-10-01", {"a.jpg": b"a"})

    def exdev(*_: object, **__: object) -> None:
        raise OSError(errno.EXDEV, os.strerror(errno.EXDEV))

    monkeypatch.setattr(trash.os, "rename", exdev)
    lines = migrate_legacy(photos, ["小明"])
    monkeypatch.undo()
    assert any("cross-device" in line for line in lines)
    assert (photos / ".trash" / "小明" / "2026-10-01" / "a.jpg").read_bytes() == b"a"
    assert _count(lib) == (0, 0)


def test_migrate_legacy_survives_a_file_vs_folder_collision(
    photos_tree: tuple[Path, Path],
) -> None:
    photos, lib = photos_tree
    other = photos / "Ann"
    other.mkdir()
    _legacy_day(photos, "小明", "2026-10-01", {"2024/a.jpg": b"a"})
    _legacy_day(photos, "Ann", "2026-10-01", {"b.jpg": b"b"})
    clash = _day(lib, "2026-10-01", {})
    (clash / "2024").write_bytes(b"a file where the legacy tree has a folder")
    lines = migrate_legacy(photos, ["小明", "Ann"])
    assert any(line.startswith("warning:") and "2026-10-01" in line and "小明" in line
               for line in lines)
    assert (photos / ".trash" / "小明" / "2026-10-01" / "2024" / "a.jpg").read_bytes() == b"a"
    moved = other / ".icloud2nas" / "trash" / "2026-10-01" / "b.jpg"
    assert moved.read_bytes() == b"b"  # the next tree still migrated


# -- root never writes or deletes through a symlink planted in a library (final review) ------


def _outside(photos: Path) -> Path:
    outside = photos.parent / "etc"  # stands in for /etc: root-owned, outside every library
    outside.mkdir()
    return outside


def test_keep_refuses_a_symlinked_folder_inside_kept(photos_tree: tuple[Path, Path]) -> None:
    photos, lib = photos_tree
    outside = _outside(photos)
    d = _day(lib, "2026-10-01", {"2024/05/x.HEIC": b"h", "y.jpg": b"j"})
    kept = lib / ".icloud2nas" / "kept"
    kept.mkdir(parents=True)
    (kept / "2024").symlink_to(outside)
    with pytest.raises(ValueError, match="symlink"):
        keep_files(lib, date(2026, 10, 1), ["y.jpg", "2024/05/x.HEIC"], photos=lib.parent)
    assert list(outside.iterdir()) == []  # nothing written outside
    assert (d / "y.jpg").exists() and (d / "2024/05/x.HEIC").exists()  # nothing moved at all


def test_keep_never_copies(
    photos_tree: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    import errno
    import os

    from icloud2nas.engine import trash

    _, lib = photos_tree
    d = _day(lib, "2026-10-01", {"y.jpg": b"j"})

    def exdev(*_: object, **__: object) -> None:
        raise OSError(errno.EXDEV, os.strerror(errno.EXDEV))

    monkeypatch.setattr(trash.os, "link", exdev)
    with pytest.raises(ValueError, match="cross-device"):
        keep_files(lib, date(2026, 10, 1), ["y.jpg"], photos=lib.parent)
    monkeypatch.undo()
    assert (d / "y.jpg").read_bytes() == b"j"
    assert _count(lib / ".icloud2nas" / "kept") == (0, 0)  # never copied


def test_migrate_legacy_refuses_a_symlinked_store(photos_tree: tuple[Path, Path]) -> None:
    photos, lib = photos_tree
    outside = _outside(photos)
    (lib / ".icloud2nas").symlink_to(outside)
    _legacy_day(photos, "小明", "2026-10-01", {"a.jpg": b"a"})
    (photos / "kept" / "小明").mkdir(parents=True)
    (photos / "kept" / "小明" / "k.jpg").write_bytes(b"k")
    lines = migrate_legacy(photos, ["小明"])
    assert list(outside.iterdir()) == []
    assert (photos / ".trash" / "小明" / "2026-10-01" / "a.jpg").read_bytes() == b"a"
    assert (photos / "kept" / "小明" / "k.jpg").read_bytes() == b"k"
    assert sum("symlink" in line for line in lines) == 2


def test_migrate_legacy_refuses_a_symlink_inside_an_existing_kept(
    photos_tree: tuple[Path, Path],
) -> None:
    photos, lib = photos_tree
    outside = _outside(photos)
    kept = lib / ".icloud2nas" / "kept"
    kept.mkdir(parents=True)
    (kept / "2024").symlink_to(outside)
    (photos / "kept" / "小明" / "2024").mkdir(parents=True)
    (photos / "kept" / "小明" / "2024" / "k.jpg").write_bytes(b"k")
    lines = migrate_legacy(photos, ["小明"])
    assert list(outside.iterdir()) == []
    assert (photos / "kept" / "小明" / "2024" / "k.jpg").read_bytes() == b"k"
    assert any(line.startswith("warning:") and "symlink" in line for line in lines)


def test_delete_day_and_purge_never_follow_a_swapped_trash_folder(
    photos_tree: tuple[Path, Path],
) -> None:
    from icloud2nas.engine.trash import purge_expired

    _, lib = photos_tree
    decoy = lib / "decoy"  # inside the library, so a resolve() check alone lets it through
    (decoy / "2026-10-01").mkdir(parents=True)
    (decoy / "2026-10-01" / DAY_MARKER).touch()
    (decoy / "2026-10-01" / "precious.jpg").write_bytes(b"p")
    (lib / ".icloud2nas").mkdir()
    (lib / ".icloud2nas" / "trash").symlink_to(decoy)
    with pytest.raises(ValueError):
        delete_day(lib, date(2026, 10, 1), photos=lib.parent)
    with pytest.raises(ValueError):  # the caller logs and skips this library
        purge_expired(lib, 1, date(2026, 12, 1), photos=lib.parent)
    assert (decoy / "2026-10-01" / "precious.jpg").read_bytes() == b"p"


def test_keep_names_a_clash_with_a_tilde_and_never_overwrites(
    photos_tree: tuple[Path, Path],
) -> None:
    _, lib = photos_tree
    d = _day(lib, "2026-10-01", {"a.jpg": b"new-a", "b.jpg": b"new-b"})
    kept = lib / ".icloud2nas" / "kept"
    kept.mkdir(parents=True)
    (kept / "b.jpg").write_bytes(b"old-b")
    (kept / "b~1.jpg").write_bytes(b"old-b1")
    out = keep_files(lib, date(2026, 10, 1), ["a.jpg", "b.jpg"], photos=lib.parent)
    assert out == [kept / "a.jpg", kept / "b~2.jpg"]
    assert (kept / "b.jpg").read_bytes() == b"old-b"
    assert (kept / "b~1.jpg").read_bytes() == b"old-b1"
    assert (kept / "b~2.jpg").read_bytes() == b"new-b" and (kept / "a.jpg").read_bytes() == b"new-a"
    assert not (d / "a.jpg").exists() and not (d / "b.jpg").exists()


def test_keep_retries_when_a_name_appears_after_validation(
    photos_tree: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    import os

    from icloud2nas.engine import trash

    _, lib = photos_tree
    d = _day(lib, "2026-10-01", {"b.jpg": b"new-b"})
    kept = lib / ".icloud2nas" / "kept"
    real_link = os.link

    def racing_link(src: str, dst: str, **kw: object) -> None:
        if dst == "b.jpg" and not (kept / "b.jpg").exists():
            (kept / "b.jpg").write_bytes(b"raced")  # another writer wins the name first
        real_link(src, dst, **kw)  # type: ignore[arg-type]

    monkeypatch.setattr(trash.os, "link", racing_link)
    out = keep_files(lib, date(2026, 10, 1), ["b.jpg"], photos=lib.parent)
    monkeypatch.undo()
    assert out == [kept / "b~1.jpg"]
    assert (kept / "b.jpg").read_bytes() == b"raced" and out[0].read_bytes() == b"new-b"
    assert not (d / "b.jpg").exists()


def test_keep_refuses_a_symlinked_folder_inside_the_day(photos_tree: tuple[Path, Path]) -> None:
    photos, lib = photos_tree
    outside = _outside(photos)
    (outside / "x.jpg").write_bytes(b"not a trash file")
    d = _day(lib, "2026-10-01", {"y.jpg": b"j"})
    (d / "2024").symlink_to(outside)
    with pytest.raises(ValueError, match="symlink"):
        keep_files(lib, date(2026, 10, 1), ["y.jpg", "2024/x.jpg"], photos=lib.parent)
    assert (outside / "x.jpg").read_bytes() == b"not a trash file"
    assert (d / "y.jpg").exists() and not (lib / ".icloud2nas" / "kept").exists()


# -- the library itself is reached from the photos root (follow-up fix 3) ---------------------


def _victim_and_swapped(photos: Path) -> tuple[Path, Path, Path]:
    """Ann's real library (with a trash day and a kept file) and Eve's library path whose
    parent Eve swapped for a link to Ann's home: (ann_lib, eve_lib, ann_day)."""
    ann_lib = photos / "Ann" / "iCloud"
    ann_day = _day(ann_lib, "2026-10-01", {"a.jpg": b"ann"})
    (ann_lib / ".icloud2nas" / "kept").mkdir()
    (ann_lib / ".icloud2nas" / "kept" / "k.jpg").write_bytes(b"ann-kept")
    (photos / "Eve").symlink_to(photos / "Ann")
    return ann_lib, photos / "Eve" / "iCloud", ann_day


def _untouched(ann_lib: Path, ann_day: Path) -> None:
    assert (ann_day / "a.jpg").read_bytes() == b"ann" and (ann_day / DAY_MARKER).is_file()
    assert (ann_lib / ".icloud2nas" / "kept" / "k.jpg").read_bytes() == b"ann-kept"
    assert sorted(p.name for p in (ann_lib / ".icloud2nas" / "kept").iterdir()) == ["k.jpg"]


def test_a_symlinked_parent_of_the_library_is_refused_everywhere(
    photos_tree: tuple[Path, Path],
) -> None:
    from icloud2nas.engine.trash import purge_expired

    photos, _ = photos_tree
    ann_lib, eve_lib, ann_day = _victim_and_swapped(photos)
    day = date(2026, 10, 1)
    with pytest.raises(ValueError, match="symlink"):
        keep_files(eve_lib, day, ["a.jpg"], photos=photos)
    with pytest.raises(ValueError, match="symlink"):
        delete_day(eve_lib, day, photos=photos)
    with pytest.raises(ValueError, match="symlink"):
        purge_expired(eve_lib, 1, date(2026, 12, 1), photos=photos)
    with pytest.raises(ValueError, match="symlink"):
        list_day(eve_lib, day, photos=photos)
    with pytest.raises(ValueError, match="symlink"):
        list_days(eve_lib, photos=photos)
    assert trash_bytes([eve_lib], photos=photos) == 0
    _untouched(ann_lib, ann_day)
    # the same calls through Ann's own path still work
    assert [d.files for d in list_days(ann_lib, photos=photos)] == [1]


def test_trash_library_dir_refuses_a_symlinked_parent(photos_tree: tuple[Path, Path]) -> None:
    from icloud2nas.engine.trash import trash_library_dir

    photos, _ = photos_tree
    ann_lib, eve_lib, ann_day = _victim_and_swapped(photos)
    with pytest.raises(ValueError, match="symlink"):
        trash_library_dir(eve_lib, date(2026, 10, 23), photos=photos)
    assert ann_lib.is_dir() and not (photos / "Ann" / ".icloud2nas-deleted").exists()
    _untouched(ann_lib, ann_day)


def test_migrate_legacy_refuses_a_symlinked_parent_of_the_library(
    photos_tree: tuple[Path, Path],
) -> None:
    photos, _ = photos_tree
    ann_lib, _eve_lib, ann_day = _victim_and_swapped(photos)
    _legacy_day(photos, "Eve/iCloud", "2026-10-01", {"e.jpg": b"eve"})
    lines = migrate_legacy(photos, ["Eve/iCloud"])
    assert any(line.startswith("warning:") and "symlink" in line for line in lines)
    assert (photos / ".trash" / "Eve" / "iCloud" / "2026-10-01" / "e.jpg").read_bytes() == b"eve"
    assert not (ann_day / "e.jpg").exists()
    _untouched(ann_lib, ann_day)


def test_keep_reports_a_trash_copy_it_could_not_remove(
    photos_tree: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    import errno
    import os

    from icloud2nas.engine import trash

    photos, lib = photos_tree
    d = _day(lib, "2026-10-01", {"y.jpg": b"j"})

    def stuck(*_: object, **__: object) -> None:
        raise OSError(errno.EROFS, os.strerror(errno.EROFS))

    monkeypatch.setattr(trash.os, "unlink", stuck)
    with pytest.raises(ValueError, match="trash copy was not removed"):
        keep_files(lib, date(2026, 10, 1), ["y.jpg"], photos=photos)
    monkeypatch.undo()
    assert (lib / ".icloud2nas" / "kept" / "y.jpg").read_bytes() == b"j"  # nothing lost
    assert (d / "y.jpg").read_bytes() == b"j"
    assert any("could not remove the trash copy" in r.message for r in caplog.records)
