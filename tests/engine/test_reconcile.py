import os
from datetime import date
from pathlib import Path

import pytest

from icloud2nas.engine.reconcile import (
    ReconcilePlan,
    apply_paths,
    compute_plan,
    is_fused,
    read_plan,
    write_plan,
)


def test_empty_remote_listing_fuses() -> None:
    assert compute_plan(set(), {"/l/a.HEIC", "/l/b.HEIC"}, "/l", remote_total=0).fused is True
    assert compute_plan(set(), set(), "/l", remote_total=0).fused is False


@pytest.mark.parametrize(
    ("extraneous", "local_total", "fused"),
    [
        (0, 100, False),
        (20, 100, False),  # exactly the floor: not fused
        (21, 100, True),  # over floor and over 20%
        (450, 10000, False),  # under 500 and under 20%
        (501, 10000, True),  # over 500 regardless of ratio
        (5, 5, False),  # tiny library: floor protects it
        (21, 5, True),
    ],
)
def test_is_fused(extraneous: int, local_total: int, fused: bool) -> None:
    assert is_fused(extraneous, local_total) is fused


def test_compute_plan_lists_sorted_relative_extraneous(tmp_path: Path) -> None:
    lib = str(tmp_path / "lib")
    expected = {os.path.join(lib, "2024", "05", "a.HEIC")}
    local = {
        os.path.join(lib, "2024", "05", "a.HEIC"),
        os.path.join(lib, "2024", "05", "z.HEIC"),
        os.path.join(lib, "2023", "01", "b.MOV"),
    }
    plan = compute_plan(expected, local, lib, remote_total=1)
    assert plan.extraneous == ["2023/01/b.MOV", "2024/05/z.HEIC"]
    assert plan.local_total == 3 and plan.remote_total == 1 and plan.fused is False


def test_plan_roundtrip(tmp_path: Path) -> None:
    plan = ReconcilePlan(str(tmp_path), 0, 2, ["2024/05/照片.HEIC", "x.MOV"])
    path = tmp_path / "plans" / "r1.txt"
    write_plan(plan, path)
    assert read_plan(path) == ["2024/05/照片.HEIC", "x.MOV"]
    write_plan(ReconcilePlan(str(tmp_path), 0, 0, []), path)
    assert read_plan(path) == []


def test_apply_moves_existing_and_skips_missing(photos_tree: tuple[Path, Path]) -> None:
    photos, lib = photos_tree
    (lib / "2024" / "05").mkdir(parents=True)
    (lib / "2024" / "05" / "gone.HEIC").write_bytes(b"1")
    (lib / "2024" / "05" / "stay.HEIC").write_bytes(b"2")
    moved = apply_paths(
        ["2024/05/gone.HEIC", "2024/05/never-existed.HEIC"],
        str(lib),
        str(photos),
        "旭刚",
        date(2026, 10, 14),
    )
    assert moved == 1
    assert not (lib / "2024" / "05" / "gone.HEIC").exists()
    assert (lib / "2024" / "05" / "stay.HEIC").exists()
    assert (photos / ".trash" / "旭刚" / "2026-10-14" / "2024" / "05" / "gone.HEIC").exists()


def test_apply_refuses_escaping_paths(photos_tree: tuple[Path, Path]) -> None:
    photos, lib = photos_tree
    with pytest.raises(ValueError):
        apply_paths(["../other.HEIC"], str(lib), str(photos), "旭刚")
    with pytest.raises(ValueError):
        apply_paths(["/etc/passwd"], str(lib), str(photos), "旭刚")
