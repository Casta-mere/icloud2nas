from __future__ import annotations

from datetime import date
from pathlib import Path

from icloud2nas.engine.trash import DAY_MARKER, trash_directory


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
