from importlib.metadata import version

import icloud2nas


def test_package_version() -> None:
    assert icloud2nas.__version__ == "0.0.1"


def test_icloudpd_is_pinned() -> None:
    assert version("icloudpd") == "1.32.3"


def test_icloudpd_entry_points_import() -> None:
    from icloudpd.base import core_single_run  # noqa: F401
    from pyicloud_ipd.services.photos import PhotoAsset  # noqa: F401
