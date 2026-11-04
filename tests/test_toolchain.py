import json
import tomllib
from importlib.metadata import version
from pathlib import Path

import icloud2nas

ROOT = Path(__file__).resolve().parents[1]


def test_every_version_field_agrees() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    v = pyproject["project"]["version"]
    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    (locked,) = [p["version"] for p in lock["package"] if p["name"] == "icloud2nas"]
    package = json.loads((ROOT / "web" / "package.json").read_text(encoding="utf-8"))
    package_lock = json.loads((ROOT / "web" / "package-lock.json").read_text(encoding="utf-8"))

    assert icloud2nas.__version__ == v
    assert locked == v
    assert package["version"] == v
    assert package_lock["version"] == v
    assert package_lock["packages"][""]["version"] == v


def test_icloudpd_is_pinned() -> None:
    assert version("icloudpd") == "1.32.3"


def test_icloudpd_entry_points_import() -> None:
    from icloudpd.base import core_single_run  # noqa: F401
    from pyicloud_ipd.services.photos import PhotoAsset  # noqa: F401
