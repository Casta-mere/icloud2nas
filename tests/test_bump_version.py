import difflib
import json
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "bump_version.py"
sys.path.insert(0, str(ROOT / "scripts"))

from bump_version import mismatches, next_version, read_version, write_version  # noqa: E402

FILES = [
    "pyproject.toml",
    "uv.lock",
    "src/icloud2nas/__init__.py",
    "web/package.json",
    "web/package-lock.json",
]


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A copy of the five files that carry the version, at their real relative paths."""
    for rel in FILES:
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / rel, tmp_path / rel)
    return tmp_path


def run(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args, "--root", str(root)],
        capture_output=True,
        text=True,
        check=False,
    )


def fields(root: Path) -> dict[str, str]:
    """Every version field, read independently of the script under test."""
    pyproject = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    lock = tomllib.loads((root / "uv.lock").read_text(encoding="utf-8"))
    (locked,) = [p["version"] for p in lock["package"] if p["name"] == "icloud2nas"]
    init = (root / "src/icloud2nas/__init__.py").read_text(encoding="utf-8")
    (dunder,) = re.findall(r'^__version__ = "([^"]*)"$', init, re.M)
    package = json.loads((root / "web/package.json").read_text(encoding="utf-8"))
    package_lock = json.loads((root / "web/package-lock.json").read_text(encoding="utf-8"))
    return {
        "pyproject": pyproject["project"]["version"],
        "init": dunder,
        "package": package["version"],
        "package-lock": package_lock["version"],
        "package-lock root": package_lock["packages"][""]["version"],
        "uv.lock": locked,
    }


def snapshot(root: Path) -> dict[str, bytes]:
    return {rel: (root / rel).read_bytes() for rel in FILES}


def test_next_version() -> None:
    assert next_version("0.1.2", "major") == "1.0.0"
    assert next_version("0.1.2", "minor") == "0.2.0"
    assert next_version("0.1.2", "patch") == "0.1.3"
    assert next_version("9.10.19", "patch") == "9.10.20"
    for bad in ("0.2.0-rc.1", "1.2", "v1.2.3", "1.2.3.4", ""):
        with pytest.raises(ValueError):
            next_version(bad, "patch")


def test_current_and_next_print_versions(repo: Path) -> None:
    current = read_version(repo)
    assert run(repo, "current").stdout == f"{current}\n"
    assert run(repo, "next", "minor").stdout == f"{next_version(current, 'minor')}\n"


def test_set_writes_four_fields_and_check_flags_uv_lock(repo: Path) -> None:
    current = read_version(repo)
    new = next_version(current, "patch")

    result = run(repo, "set", "patch")

    assert result.returncode == 0, result.stderr
    assert result.stdout == f"{new}\n"
    found = fields(repo)
    assert found.pop("uv.lock") == current
    assert set(found.values()) == {new}
    check = run(repo, "check")
    assert check.returncode == 1
    (line,) = check.stdout.splitlines()
    assert line.startswith("uv.lock") and current in line and new in line


def test_json_files_change_only_version_lines(repo: Path) -> None:
    current = read_version(repo)
    before = {rel: (repo / rel).read_text(encoding="utf-8") for rel in FILES}

    assert run(repo, "set", "9.9.9").returncode == 0

    for rel, count in (("web/package.json", 1), ("web/package-lock.json", 2)):
        after = (repo / rel).read_text(encoding="utf-8")
        diff = list(
            difflib.unified_diff(
                before[rel].splitlines(keepends=True), after.splitlines(keepends=True), n=0
            )
        )
        removed = [line[1:] for line in diff if line[:1] == "-" and line[:3] != "---"]
        added = [line[1:] for line in diff if line[:1] == "+" and line[:3] != "+++"]
        assert len(removed) == count, "".join(diff)
        assert [line.replace(current, "9.9.9") for line in removed] == added, "".join(diff)
        assert all(re.fullmatch(r' +"version": "9\.9\.9",\n', line) for line in added)


def test_set_explicit_version(repo: Path) -> None:
    result = run(repo, "set", "2.3.4")

    assert result.returncode == 0, result.stderr
    assert result.stdout == "2.3.4\n"
    found = fields(repo)
    del found["uv.lock"]  # only `uv lock` rewrites it
    assert set(found.values()) == {"2.3.4"}


@pytest.mark.parametrize("target", ["patch", "1.0.0"])
def test_set_refuses_non_release_current(repo: Path, target: str) -> None:
    pyproject = repo / "pyproject.toml"
    text = pyproject.read_text(encoding="utf-8")
    pyproject.write_text(
        re.sub(r'^version = "[^"]*"$', 'version = "0.2.0-rc.1"', text, count=1, flags=re.M),
        encoding="utf-8",
    )
    before = snapshot(repo)

    result = run(repo, "set", target)

    assert result.returncode != 0
    assert "0.2.0-rc.1" in result.stderr
    assert "fix project.version in pyproject.toml by hand" in result.stderr
    assert snapshot(repo) == before
    with pytest.raises(ValueError, match="0.2.0-rc.1"):
        write_version(repo, "1.0.0")
    assert snapshot(repo) == before


@pytest.mark.parametrize("target", ["0.2.0-rc.1", "1.2", "v1.2.3", "banana"])
def test_set_refuses_a_target_that_is_not_x_y_z(repo: Path, target: str) -> None:
    before = snapshot(repo)

    result = run(repo, "set", target)

    assert result.returncode != 0
    assert snapshot(repo) == before


def test_check_passes_on_the_real_repo() -> None:
    assert mismatches(ROOT) == []
    assert run(ROOT, "check").returncode == 0
