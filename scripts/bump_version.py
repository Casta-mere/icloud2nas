"""Read, bump and check the version fields that must agree (see docs/release.md).

    python scripts/bump_version.py current           print pyproject.toml's version
    python scripts/bump_version.py next PART         print the version a PART bump gives
    python scripts/bump_version.py set PART|X.Y.Z    write the four hand-editable fields
    python scripts/bump_version.py check             list fields that disagree; exit 1 if any

Every command takes `--root DIR` (default: this repository). `set` refuses, writing nothing,
unless pyproject.toml's current version is a plain X.Y.Z, for a PART and an explicit X.Y.Z alike.
It leaves `uv.lock` to `uv lock`; `make bump` runs all three steps. Stdlib only: the release
workflow runs it on the runner's python3 too.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tomllib
from pathlib import Path
from typing import Any, Literal, cast, get_args

Part = Literal["major", "minor", "patch"]
PARTS: tuple[str, ...] = get_args(Part)

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = "pyproject.toml"
INIT = "src/icloud2nas/__init__.py"
PACKAGE = "web/package.json"
PACKAGE_LOCK = "web/package-lock.json"
UV_LOCK = "uv.lock"

_RELEASE = re.compile(r"([0-9]+)\.([0-9]+)\.([0-9]+)")
# One anchored line per file; the value is replaced in place so nothing else in the file moves.
_LINES = {
    PYPROJECT: re.compile(r'^(version = ")([^"\n]*)(")$', re.M),
    INIT: re.compile(r'^(__version__ = ")([^"\n]*)(")$', re.M),
}


def _release(version: str) -> tuple[int, int, int]:
    m = _RELEASE.fullmatch(version)
    if m is None:
        raise ValueError(f"{version!r} is not a plain X.Y.Z version")
    return int(m[1]), int(m[2]), int(m[3])


def _read(root: Path, rel: str) -> str:
    return (root / rel).read_text(encoding="utf-8")


def _line_value(root: Path, rel: str) -> str:
    found = _LINES[rel].findall(_read(root, rel))
    if len(found) != 1:
        raise ValueError(f"{rel}: expected exactly one version line, found {len(found)}")
    return cast(str, found[0][1])


def _json(root: Path, rel: str) -> Any:
    return json.loads(_read(root, rel))


def _dump_json(data: Any) -> str:
    # npm's own layout (JSON.stringify(data, null, 2) plus a newline), so only values change.
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def read_version(root: Path) -> str:
    """pyproject.toml's `project.version`, the field every other one must match."""
    with (root / PYPROJECT).open("rb") as f:
        return cast(str, tomllib.load(f)["project"]["version"])


def next_version(current: str, part: Part) -> str:
    major, minor, patch = _release(current)
    if part == "major":
        return f"{major + 1}.0.0"
    if part == "minor":
        return f"{major}.{minor + 1}.0"
    if part == "patch":
        return f"{major}.{minor}.{patch + 1}"
    raise ValueError(f"unknown part {part!r}; expected one of {', '.join(PARTS)}")


def _release_current(root: Path) -> str:
    """pyproject.toml's version, refused unless it is a plain X.Y.Z (whatever the bump target)."""
    current = read_version(root)
    if _RELEASE.fullmatch(current) is None:
        raise ValueError(
            f"pyproject.toml says version {current!r}, not a plain X.Y.Z, so nothing was "
            "changed; fix project.version in pyproject.toml by hand first"
        )
    return current


def write_version(root: Path, version: str) -> None:
    """Write `version` to the four hand-editable files. Nothing is written unless all can be,
    and never while the current version isn't a plain X.Y.Z."""
    _release_current(root)
    _release(version)
    out: dict[str, str] = {}
    for rel, pattern in _LINES.items():
        _line_value(root, rel)  # exactly one line, or raise before writing anything
        out[rel] = pattern.sub(lambda m: f"{m[1]}{version}{m[3]}", _read(root, rel))
    package = _json(root, PACKAGE)
    package["version"] = version
    out[PACKAGE] = _dump_json(package)
    package_lock = _json(root, PACKAGE_LOCK)
    package_lock["version"] = version
    package_lock["packages"][""]["version"] = version
    out[PACKAGE_LOCK] = _dump_json(package_lock)
    for rel, text in out.items():
        (root / rel).write_text(text, encoding="utf-8", newline="\n")


def _locked_version(root: Path) -> str | None:
    lock = tomllib.loads(_read(root, UV_LOCK))
    found = [p.get("version") for p in lock.get("package", []) if p.get("name") == "icloud2nas"]
    return cast(str, found[0]) if len(found) == 1 else None


def mismatches(root: Path) -> list[str]:
    """One line per field that disagrees with pyproject.toml; empty when they all agree."""
    expected = read_version(root)
    package = _json(root, PACKAGE)
    package_lock = _json(root, PACKAGE_LOCK)
    found = {
        UV_LOCK: (_locked_version(root), "run `uv lock`"),
        INIT: (_line_value(root, INIT), None),
        PACKAGE: (package.get("version"), None),
        f"{PACKAGE_LOCK} version": (package_lock.get("version"), None),
        f'{PACKAGE_LOCK} packages[""].version': (
            package_lock.get("packages", {}).get("", {}).get("version"),
            None,
        ),
    }
    lines = []
    for field, (value, hint) in found.items():
        if value != expected:
            line = f"{field} says {value}, pyproject.toml says {expected}"
            lines.append(f"{line} ({hint})" if hint else line)
    return lines


def main(argv: list[str] | None = None) -> int:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--root", type=Path, default=ROOT, help="repository root")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("current", parents=[common], help="print the current version")
    p_next = sub.add_parser("next", parents=[common], help="print the next version")
    p_next.add_argument("part", choices=PARTS)
    p_set = sub.add_parser("set", parents=[common], help="write a new version")
    p_set.add_argument("target", metavar="PART_OR_X.Y.Z")
    sub.add_parser("check", parents=[common], help="list fields that disagree")
    args = parser.parse_args(argv)
    root: Path = args.root

    try:
        if args.command == "current":
            print(read_version(root))
        elif args.command == "next":
            print(next_version(read_version(root), args.part))
        elif args.command == "set":
            current = _release_current(root)
            target: str = args.target
            if target in PARTS:
                target = next_version(current, cast(Part, target))
            write_version(root, target)
            print(target)
        else:
            problems = mismatches(root)
            for line in problems:
                print(line)
            return 1 if problems else 0
    except ValueError as e:
        print(f"bump_version: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
