"""Expected local paths for a remote asset, computed with icloudpd's own filename rules.

This mirrors icloudpd.base.download_builder for our fixed settings (sizes=[original],
live photo original size) so that reconcile and the recently-deleted pass agree
byte-for-byte with what the downloader wrote.
"""

from __future__ import annotations

import datetime
import os
from collections.abc import Callable
from pathlib import Path

from icloudpd.base import build_filename_cleaner, lp_filename_concatinator, lp_filename_original
from icloudpd.filename_policies import create_filename_builder
from icloudpd.paths import local_download_path
from pyicloud_ipd.asset_version import calculate_version_filename
from pyicloud_ipd.file_match import FileMatchPolicy
from pyicloud_ipd.raw_policy import RawTreatmentPolicy
from pyicloud_ipd.services.photos import PhotoAsset
from pyicloud_ipd.utils import disambiguate_filenames
from pyicloud_ipd.version_size import AssetVersionSize, LivePhotoVersionSize

from icloud2nas.engine.job import FilePolicy

SIZES: tuple[AssetVersionSize, ...] = (AssetVersionSize.ORIGINAL,)
LIVE_PHOTO_SIZE = LivePhotoVersionSize.ORIGINAL
RAW_POLICY = RawTreatmentPolicy.AS_IS


def file_match_policy(policy: FilePolicy) -> FileMatchPolicy:
    return FileMatchPolicy(policy.file_match_policy)


def lp_filename_generator(policy: FilePolicy) -> Callable[[str], str]:
    if policy.live_photo_mov_filename_policy == "original":
        return lp_filename_original
    return lp_filename_concatinator


def filename_builder(policy: FilePolicy) -> Callable[[PhotoAsset], str]:
    cleaner = build_filename_cleaner(policy.keep_unicode_in_filenames)
    return create_filename_builder(file_match_policy(policy), cleaner)


def date_dir(asset: PhotoAsset, library_dir: str, folder_structure: str) -> str:
    if folder_structure.lower() == "none":
        return os.path.normpath(library_dir)
    try:
        rel = folder_structure.format(asset.created)
    except ValueError:  # icloudpd falls back to the epoch for unrepresentable dates
        rel = folder_structure.format(datetime.datetime.fromtimestamp(0))
    return os.path.normpath(os.path.join(library_dir, rel))


def _add(paths: set[str], path: str, size: int, policy: FilePolicy) -> None:
    paths.add(os.path.normpath(path))
    if policy.file_match_policy == "name-size-dedup-with-suffix":
        # icloudpd renames to NAME-<bytes>.EXT when a same-named file of another size exists
        paths.add(os.path.normpath(f"-{size}.".join(path.rsplit(".", 1))))


def expected_paths(asset: PhotoAsset, library_dir: str, policy: FilePolicy) -> set[str]:
    """Every absolute path icloudpd would write for this asset under our fixed settings."""
    builder = filename_builder(policy)
    lp_gen = lp_filename_generator(policy)
    download_dir = date_dir(asset, library_dir, policy.folder_structure)
    paths: set[str] = set()
    versions_with_policy = asset.versions_with_raw_policy(RAW_POLICY)
    versions, overrides = disambiguate_filenames(versions_with_policy, SIZES, asset, lp_gen)
    base = builder(asset)
    for size in SIZES:
        if size not in versions:
            continue
        version = versions[size]
        name = calculate_version_filename(
            base, version, size, lp_gen, asset.item_type, overrides.get(size)
        )
        _add(paths, local_download_path(name, download_dir), version.size, policy)
    if LIVE_PHOTO_SIZE in versions_with_policy:
        version = versions_with_policy[LIVE_PHOTO_SIZE]
        name = calculate_version_filename(base, version, LIVE_PHOTO_SIZE, lp_gen, asset.item_type)
        _add(paths, os.path.join(download_dir, name), version.size, policy)
    return paths


def walk_library(library_dir: str | Path) -> set[str]:
    """Regular files under library_dir, ignoring in-flight .part files and dot-prefixed entries."""
    root = Path(library_dir)
    found: set[str] = set()
    if not root.is_dir():
        return found
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for name in filenames:
            if name.startswith(".") or name.endswith(".part"):
                continue
            found.add(os.path.normpath(os.path.join(dirpath, name)))
    return found
