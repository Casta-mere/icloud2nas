"""Full reconciliation: local files that match no remote asset go to the trash, with a fuse."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from icloud2nas.engine.trash import move_to_trash

FUSE_MAX_FILES = 500
FUSE_MAX_RATIO = 0.2
FUSE_RATIO_FLOOR = 20


def is_fused(
    extraneous: int,
    local_total: int,
    *,
    max_files: int = FUSE_MAX_FILES,
    max_ratio: float = FUSE_MAX_RATIO,
    ratio_floor: int = FUSE_RATIO_FLOOR,
) -> bool:
    """True when the deletion is too large to run unattended (spec §8.3 step 5)."""
    if extraneous > max_files:
        return True
    return extraneous > max(ratio_floor, int(local_total * max_ratio))


@dataclass
class ReconcilePlan:
    library_dir: str
    remote_total: int
    local_total: int
    extraneous: list[str] = field(default_factory=list)  # relative to library_dir, sorted

    @property
    def fused(self) -> bool:
        if self.remote_total == 0 and self.local_total > 0:
            return True  # an empty listing is far more likely an API hiccup than a wiped library
        return is_fused(len(self.extraneous), self.local_total)


def compute_plan(
    expected: set[str], local: set[str], library_dir: str, remote_total: int
) -> ReconcilePlan:
    root = os.path.normpath(library_dir)
    extra = sorted(os.path.relpath(p, root) for p in local - expected)
    return ReconcilePlan(
        library_dir=root, remote_total=remote_total, local_total=len(local), extraneous=extra
    )


def write_plan(plan: ReconcilePlan, path: str | Path) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    body = "\n".join(plan.extraneous)
    target.write_text(body + ("\n" if body else ""), encoding="utf-8")


def read_plan(path: str | Path) -> list[str]:
    return [line for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def apply_paths(rel_paths: list[str], library_dir: str, day: date | None = None) -> int:
    """Trash each still-existing file. Missing files are skipped. Returns the number moved."""
    moved = 0
    for rel in rel_paths:
        norm = os.path.normpath(rel)
        if os.path.isabs(norm) or norm == ".." or norm.startswith(".." + os.sep):
            raise ValueError(f"refusing path outside library: {rel}")
        abs_path = os.path.join(library_dir, norm)
        if not os.path.isfile(abs_path):
            continue
        move_to_trash(library_dir, abs_path, day)
        moved += 1
    return moved
