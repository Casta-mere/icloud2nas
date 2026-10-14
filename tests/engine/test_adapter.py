import json
import logging
from typing import Any

import pytest
from icloudpd.mfa_provider import MFAProvider
from icloudpd.password_provider import PasswordProvider
from icloudpd.status import StatusExchange
from pyicloud_ipd.file_match import FileMatchPolicy
from pyicloud_ipd.live_photo_mov_filename_policy import LivePhotoMovFilenamePolicy
from pyicloud_ipd.version_size import AssetVersionSize, LivePhotoVersionSize

from icloud2nas.engine import icloudpd_adapter as adapter
from icloud2nas.engine.job import JobSpec
from tests.fakes import FakeICloud, FakeLibrary

BASE = {
    "account_id": "acc1",
    "apple_id": "a@icloud.com",
    "region": "cn",
    "client_id": "11111111-1111-1111-1111-111111111111",
    "cookie_dir": "/data/accounts/acc1/cookies",
    "photos_root": "/photos",
    "target_dir": "/photos/旭刚",
}


def _spec(mode: str = "sync", **extra: Any) -> JobSpec:
    return JobSpec.load(mode, "run-1", json.dumps({**BASE, **extra}))


def test_check_touchpoints_passes_on_pinned_version() -> None:
    adapter.check_touchpoints()


def test_check_touchpoints_fails_on_version_drift(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(adapter, "ICLOUDPD_VERSION", "9.9.9")
    with pytest.raises(RuntimeError, match="9.9.9"):
        adapter.check_touchpoints()


def test_global_config_is_non_interactive_webui_parameter() -> None:
    cfg = adapter.build_global_config(_spec())
    assert cfg.domain == "cn" and cfg.watch_with_interval is None
    assert cfg.mfa_provider is MFAProvider.WEBUI
    assert list(cfg.password_providers) == [PasswordProvider.PARAMETER]
    assert cfg.threads_num == 2 and cfg.no_progress_bar is True


def test_user_config_fixed_download_settings() -> None:
    cfg = adapter.build_user_config(_spec())
    assert cfg.username == "a@icloud.com" and cfg.password is None
    assert cfg.directory == "/photos/旭刚" and cfg.cookie_directory == BASE["cookie_dir"]
    assert list(cfg.sizes) == [AssetVersionSize.ORIGINAL]
    assert cfg.live_photo_size is LivePhotoVersionSize.ORIGINAL
    assert cfg.auto_delete is False and cfg.xmp_sidecar is False
    assert cfg.set_exif_datetime is False and cfg.dry_run is False
    assert cfg.library == "" and cfg.until_found == 50 and cfg.recent is None
    assert cfg.folder_structure == "{:%Y/%m}"
    assert cfg.file_match_policy is FileMatchPolicy.NAME_ID7
    assert cfg.live_photo_mov_filename_policy is LivePhotoMovFilenamePolicy.ORIGINAL
    assert cfg.keep_unicode_in_filenames is True


def test_user_config_full_and_shared_and_recent() -> None:
    cfg = adapter.build_user_config(
        _spec(full=True, library_zone="SharedSync-X", recent=20, dry_run=True)
    )
    assert cfg.until_found is None and cfg.library == "SharedSync-X"
    assert cfg.recent == 20 and cfg.dry_run is True


def test_password_providers_return_password() -> None:
    providers = adapter.build_password_providers("s3cret")
    reader, writer = providers[PasswordProvider.PARAMETER]
    assert reader("anyone") == "s3cret"
    assert writer("anyone", "ignored") is None


def test_authenticate_passes_spec_to_icloudpd(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_authenticator(*args: Any) -> str:
        captured["args"] = args
        return "icloud"

    monkeypatch.setattr(adapter, "authenticator", fake_authenticator)
    exchange = StatusExchange()
    logger = logging.getLogger("test")

    def notificator() -> None:
        return None

    result = adapter.authenticate(_spec("auth"), "pw", exchange, notificator, logger)
    assert result == "icloud"
    (lg, domain, providers, mfa, ex, username, notif, observer, cookie_dir, client_id) = captured[
        "args"
    ]
    assert lg is logger and domain == "cn" and mfa is MFAProvider.WEBUI and ex is exchange
    assert username == "a@icloud.com" and notif is notificator and observer is None
    assert cookie_dir == BASE["cookie_dir"] and client_id == BASE["client_id"]
    assert list(providers) == ["parameter"] and providers["parameter"][0]("u") == "pw"


def test_list_and_get_library() -> None:
    icloud = FakeICloud()
    shared = FakeLibrary()
    icloud.photos.private_libraries = {"PrimarySync": icloud.photos}
    icloud.photos.shared_libraries = {"SharedSync-B": shared, "SharedSync-A": FakeLibrary()}
    assert adapter.list_libraries(icloud) == (["PrimarySync"], ["SharedSync-A", "SharedSync-B"])  # type: ignore[arg-type]
    assert adapter.get_library(icloud, None) is icloud.photos  # type: ignore[arg-type]
    assert adapter.get_library(icloud, "PrimarySync") is icloud.photos  # type: ignore[arg-type]
    assert adapter.get_library(icloud, "SharedSync-B") is shared  # type: ignore[arg-type]
    with pytest.raises(KeyError):
        adapter.get_library(icloud, "SharedSync-Z")  # type: ignore[arg-type]


def test_iterators_and_count() -> None:
    lib = FakeLibrary(all=["a", "b"], recently_deleted=["c"])  # type: ignore[list-item]
    assert list(adapter.iter_all(lib)) == ["a", "b"]  # type: ignore[arg-type]
    assert list(adapter.iter_recently_deleted(lib)) == ["c"]  # type: ignore[arg-type]
    assert adapter.count_all(lib) == 2  # type: ignore[arg-type]


def test_run_sync_wires_core_single_run(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_core(*args: Any) -> int:
        captured["args"] = args
        return 0

    monkeypatch.setattr(adapter, "core_single_run", fake_core)
    exchange = StatusExchange()
    logger = logging.getLogger("test")
    rc = adapter.run_sync(_spec(), "pw", exchange, lambda: None, logger)
    assert rc == 0
    (lg, ex, gcfg, ucfg, providers, passer, downloader, notif, lp_gen) = captured["args"]
    assert lg is logger and ex is exchange and gcfg.domain == "cn"
    assert ucfg.auto_delete is False and providers[PasswordProvider.PARAMETER][0]("u") == "pw"
    assert callable(passer) and callable(downloader) and callable(lp_gen)
    assert exchange.get_current_user() == "a@icloud.com"
