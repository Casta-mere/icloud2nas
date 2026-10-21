from __future__ import annotations

import pytest

from icloud2nas.orchestrator.config import Config
from icloud2nas.orchestrator.settings import NOTIFY_EVENTS, Settings
from icloud2nas.orchestrator.store import repo
from icloud2nas.orchestrator.store.db import Store, make_engine, upgrade_db


@pytest.fixture
def store(config: Config) -> Store:
    engine = make_engine(config.db_path)
    upgrade_db(engine)
    return Store(engine)


def test_defaults_and_round_trip(store: Store) -> None:
    st = Settings(store)
    assert st.get("max_workers") == 2 and st.get("trash_retention_days") == 30
    assert st.get("log_retention_runs") == 20 and st.get("threads_per_worker") == 2
    assert st.get("file_match_policy") == "name-id7"
    st.set("max_workers", "3")
    assert st.get("max_workers") == 3
    with pytest.raises(ValueError, match="max_workers"):
        st.set("max_workers", "0")
    with pytest.raises(ValueError, match="file_match_policy"):
        st.set("file_match_policy", "exact")
    with pytest.raises(ValueError, match="nightly_default"):
        st.set("nightly_default", "x")  # unknown key
    assert "admin_password_hash" not in st.all() and "admin_password_set_at" not in st.all()
    assert st.all()["max_workers"] == 3


def test_validation_rules(store: Store) -> None:
    st = Settings(store)
    for key, bad in (
        ("max_workers", "9"),
        ("max_workers", "abc"),
        ("min_free_gb", "-1"),
        ("threads_per_worker", "0"),
        ("trash_retention_days", "-1"),
        ("log_retention_runs", "0"),
        ("live_photo_mov_filename_policy", "x"),
        ("keep_unicode_in_filenames", "maybe"),
        ("folder_structure", "plain"),
        ("paused", "2"),
        ("public_url", "ftp://x"),
        ("notify.needs_code.enabled", "x"),
    ):
        with pytest.raises(ValueError, match=key.replace(".", r"\.")):
            st.set(key, bad)
    st.set("public_url", "")
    st.set("public_url", "https://i2n.example")
    st.set("paused", "TRUE")
    assert st.get("paused") is True


def test_set_many_is_atomic(store: Store) -> None:
    st = Settings(store)
    with pytest.raises(ValueError, match="min_free_gb"):
        st.set_many({"max_workers": "4", "min_free_gb": "-1"})
    assert st.get("max_workers") == 2
    st.set_many({"max_workers": "4", "min_free_gb": "1.5"})
    assert st.get("max_workers") == 4 and st.get("min_free_gb") == 1.5


def test_env_seeds_only_absent_keys(store: Store) -> None:
    st = Settings(store)
    st.seed_from_env({"I2N_MAX_WORKERS": "4"})
    assert st.get("max_workers") == 4
    st.set("max_workers", "1")
    st.seed_from_env({"I2N_MAX_WORKERS": "4"})
    assert st.get("max_workers") == 1


def test_policy_maps_threads_per_worker(store: Store) -> None:
    st = Settings(store)
    st.set("threads_per_worker", "3")
    policy = st.policy()
    assert policy["threads_num"] == 3 and policy["file_match_policy"] == "name-id7"
    assert policy["keep_unicode_in_filenames"] is True and policy["folder_structure"] == "{:%Y/%m}"


def test_notify_toggles_and_legacy_paused_value(store: Store) -> None:
    st = Settings(store)
    assert all(st.notify_enabled(e) for e in NOTIFY_EVENTS)
    st.set("notify.space_low.enabled", "0")
    assert not st.notify_enabled("space_low") and st.notify_enabled("needs_code")
    with pytest.raises(ValueError):
        st.notify_enabled("nope")
    with store.session() as s:
        repo.set_setting(s, "paused", "1")
    assert st.get("paused") is True
