"""The only module that drives icloudpd / pyicloud_ipd internals. Written against icloudpd 1.32.3.

Touchpoints (keep this list in sync with check_touchpoints):
  1. GlobalConfig / UserConfig construction and icloudpd.base.core_single_run
  2. icloudpd.authentication.authenticator with MFAProvider.WEBUI and a caller-owned StatusExchange
  3. the `notificator` callback icloudpd fires when Apple asks for a 2FA code
  4. PhotoLibrary.all / .recently_deleted iteration and PhotosService.private/shared_libraries
  5. filename/path helpers used by engine.paths (download_builder parity)
"""

from __future__ import annotations

import inspect
import logging
import os
from collections.abc import Callable, Iterator
from functools import partial
from importlib.metadata import version as installed_version

from icloudpd.authentication import authenticator
from icloudpd.base import core_single_run, download_builder, where_builder
from icloudpd.config import GlobalConfig, UserConfig
from icloudpd.log_level import LogLevel
from icloudpd.mfa_provider import MFAProvider
from icloudpd.password_provider import PasswordProvider
from icloudpd.status import StatusExchange
from pyicloud_ipd.base import PyiCloudService
from pyicloud_ipd.file_match import FileMatchPolicy
from pyicloud_ipd.live_photo_mov_filename_policy import LivePhotoMovFilenamePolicy
from pyicloud_ipd.raw_policy import RawTreatmentPolicy
from pyicloud_ipd.services.photos import PhotoAsset, PhotoLibrary, PhotosService
from pyicloud_ipd.version_size import AssetVersionSize, LivePhotoVersionSize

from icloud2nas.engine.job import JobSpec
from icloud2nas.engine.paths import filename_builder, lp_filename_generator

__all__ = [
    "ICLOUDPD_VERSION",
    "PRIMARY_ZONE",
    "PhotoAsset",
    "PhotoLibrary",
    "PyiCloudService",
    "authenticate",
    "build_global_config",
    "build_password_providers",
    "build_user_config",
    "check_touchpoints",
    "count_all",
    "get_library",
    "iter_all",
    "iter_recently_deleted",
    "list_libraries",
    "run_sync",
]

ICLOUDPD_VERSION = "1.32.3"
PRIMARY_ZONE = "PrimarySync"

PasswordReader = Callable[[str], str | None]
PasswordWriter = Callable[[str, str], None]
PasswordProviders = dict[PasswordProvider, tuple[PasswordReader, PasswordWriter]]

_EXPECTED_SIGNATURES: dict[Callable[..., object], list[str]] = {
    core_single_run: [
        "logger", "status_exchange", "global_config", "user_config", "password_providers_dict",
        "passer", "downloader", "notificator", "lp_filename_generator",
    ],
    authenticator: [
        "logger", "domain", "password_providers", "mfa_provider", "status_exchange", "username",
        "notificator", "response_observer", "cookie_directory", "client_id",
    ],
    where_builder: [
        "logger", "skip_videos", "skip_created_before", "skip_created_after", "skip_photos",
        "filename_builder", "photo",
    ],
    download_builder: [
        "logger", "folder_structure", "directory", "primary_sizes", "force_size",
        "only_print_filenames", "set_exif_datetime", "skip_live_photos", "live_photo_size",
        "dry_run", "file_match_policy", "xmp_sidecar", "lp_filename_generator",
        "filename_builder", "raw_policy", "icloud", "counter", "photo",
    ],
}


def check_touchpoints() -> None:
    """Raise RuntimeError if the installed icloudpd is not what this adapter was written for."""
    found = installed_version("icloudpd")
    if found != ICLOUDPD_VERSION:
        raise RuntimeError(f"icloudpd {found} installed, adapter written for {ICLOUDPD_VERSION}")
    for fn, names in _EXPECTED_SIGNATURES.items():
        actual = list(inspect.signature(fn).parameters)
        if actual != names:
            raise RuntimeError(f"{fn.__name__} signature changed: {actual}")
    for attr in ("replace_status", "set_payload", "set_error", "get_progress", "set_current_user"):
        if not callable(getattr(StatusExchange, attr, None)):
            raise RuntimeError(f"StatusExchange.{attr} missing")
    for attr in ("all", "recently_deleted"):
        if not isinstance(getattr(PhotoLibrary, attr, None), property):
            raise RuntimeError(f"PhotoLibrary.{attr} is no longer a property")
    for attr in ("private_libraries", "shared_libraries"):
        if not isinstance(getattr(PhotosService, attr, None), property):
            raise RuntimeError(f"PhotosService.{attr} is no longer a property")


def build_global_config(spec: JobSpec) -> GlobalConfig:
    return GlobalConfig(
        help=False,
        version=False,
        use_os_locale=False,
        only_print_filenames=False,
        log_level=LogLevel.INFO,
        no_progress_bar=True,
        threads_num=spec.policy.threads_num,
        domain=spec.region,
        watch_with_interval=None,
        password_providers=[PasswordProvider.PARAMETER],
        mfa_provider=MFAProvider.WEBUI,
    )


def build_user_config(spec: JobSpec) -> UserConfig:
    return UserConfig(
        username=spec.apple_id,
        password=None,
        directory=spec.target_dir or "",
        auth_only=False,
        cookie_directory=spec.cookie_dir,
        sizes=[AssetVersionSize.ORIGINAL],
        live_photo_size=LivePhotoVersionSize.ORIGINAL,
        recent=spec.recent,
        until_found=None if spec.full else spec.until_found,
        albums=[],
        list_albums=False,
        library="" if spec.is_primary else (spec.library_zone or ""),
        list_libraries=False,
        skip_videos=False,
        skip_live_photos=False,
        xmp_sidecar=False,
        force_size=False,
        auto_delete=False,  # deletes are ours: trash-first, see worker._recently_deleted_pass
        folder_structure=spec.policy.folder_structure,
        set_exif_datetime=False,
        smtp_username=None,
        smtp_password=None,
        smtp_host="",
        smtp_port=587,
        smtp_no_tls=False,
        notification_email=None,
        notification_email_from=None,
        notification_script=None,
        delete_after_download=False,
        keep_icloud_recent_days=None,
        dry_run=spec.dry_run,
        keep_unicode_in_filenames=spec.policy.keep_unicode_in_filenames,
        live_photo_mov_filename_policy=LivePhotoMovFilenamePolicy(
            spec.policy.live_photo_mov_filename_policy
        ),
        align_raw=RawTreatmentPolicy.AS_IS,
        file_match_policy=FileMatchPolicy(spec.policy.file_match_policy),
        skip_created_before=None,
        skip_created_after=None,
        skip_photos=False,
    )


def build_password_providers(password: str) -> PasswordProviders:
    def read(_username: str) -> str | None:
        return password

    def write(_username: str, _password: str) -> None:
        return None

    return {PasswordProvider.PARAMETER: (read, write)}


def authenticate(
    spec: JobSpec,
    password: str,
    status_exchange: StatusExchange,
    notificator: Callable[[], None],
    logger: logging.Logger,
) -> PyiCloudService:
    """Log in: saved session first, password + 2FA if needed. pyicloud exceptions propagate."""
    providers = {p.value: fns for p, fns in build_password_providers(password).items()}
    return authenticator(
        logger,
        spec.region,
        providers,
        MFAProvider.WEBUI,
        status_exchange,
        spec.apple_id,
        notificator,
        None,
        spec.cookie_dir,
        spec.client_id,
    )


def list_libraries(icloud: PyiCloudService) -> tuple[list[str], list[str]]:
    return sorted(icloud.photos.private_libraries), sorted(icloud.photos.shared_libraries)


def get_library(icloud: PyiCloudService, zone: str | None) -> PhotoLibrary:
    if zone in (None, "", PRIMARY_ZONE):
        return icloud.photos
    private = icloud.photos.private_libraries
    if zone in private:
        return private[zone]
    shared = icloud.photos.shared_libraries
    if zone in shared:
        return shared[zone]
    raise KeyError(f"unknown library zone {zone!r}")


def iter_all(library: PhotoLibrary) -> Iterator[PhotoAsset]:
    return iter(library.all)


def iter_recently_deleted(library: PhotoLibrary) -> Iterator[PhotoAsset]:
    return iter(library.recently_deleted)


def count_all(library: PhotoLibrary) -> int:
    return len(library.all)


def run_sync(
    spec: JobSpec,
    password: str,
    status_exchange: StatusExchange,
    notificator: Callable[[], None],
    logger: logging.Logger,
) -> int:
    """One icloudpd download pass: no watch loop, no deletes. Returns icloudpd's code."""
    os.environ["CLIENT_ID"] = spec.client_id  # core_single_run reads the device id from the env
    global_config = build_global_config(spec)
    user_config = build_user_config(spec)
    builder = filename_builder(spec.policy)
    lp_gen = lp_filename_generator(spec.policy)
    passer = partial(where_builder, logger, False, None, None, False, builder)
    downloader = partial(
        download_builder,
        logger,
        user_config.folder_structure,
        user_config.directory,
        user_config.sizes,
        user_config.force_size,
        global_config.only_print_filenames,
        user_config.set_exif_datetime,
        user_config.skip_live_photos,
        user_config.live_photo_size,
        user_config.dry_run,
        user_config.file_match_policy,
        user_config.xmp_sidecar,
        lp_gen,
        builder,
        user_config.align_raw,
    )
    status_exchange.set_global_config(global_config)
    status_exchange.set_user_configs([user_config])
    status_exchange.set_current_user(user_config.username)
    return core_single_run(
        logger,
        status_exchange,
        global_config,
        user_config,
        build_password_providers(password),
        passer,
        downloader,
        notificator,
        lp_gen,
    )
