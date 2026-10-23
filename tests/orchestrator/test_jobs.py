import json

import pytest

from icloud2nas.orchestrator.config import Config
from icloud2nas.orchestrator.jobs import build_job, validate_job
from icloud2nas.orchestrator.settings import Settings
from icloud2nas.orchestrator.store import repo
from icloud2nas.orchestrator.store.db import Store, make_engine, upgrade_db


@pytest.fixture
def store(config: Config) -> Store:
    engine = make_engine(config.db_path)
    upgrade_db(engine)
    return Store(engine)


def test_build_job_for_primary_and_shared(config: Config, store: Store) -> None:
    with store.session() as s:
        acc = repo.create_account(
            s, display_name="小明", apple_id="a@icloud.com", region="cn",
            password_enc=b"x", client_id="11111111-1111-1111-1111-111111111111",
        )
        primary = repo.upsert_library(
            s, acc, kind="personal", zone_name="PrimarySync", display_name="小明",
            target_dir="小明", interval_min=30, nightly_at="03:00",
        )
        shared = repo.upsert_library(
            s, acc, kind="shared", zone_name="SharedSync-ABC", display_name="家庭",
            target_dir="shared/家庭", interval_min=30, nightly_at="03:00",
        )
        job = build_job(config, acc, primary, interactive=False, full=True)
        assert job["apple_id"] == "a@icloud.com" and job["region"] == "cn"
        assert job["cookie_dir"] == str(config.cookie_dir(acc.id))
        assert job["photos_root"] == str(config.photos_dir)
        assert job["target_dir"] == str(config.photos_dir / "小明")
        assert job["plan_dir"] == str(config.plans_dir / acc.id)
        assert job["library_zone"] is None and job["full"] is True and job["interactive"] is False
        assert job["until_found"] == 50 and job["mfa_timeout_s"] == 600
        assert job["index_wait_s"] == 300
        assert "policy" not in job
        validate_job("sync", job)
        validate_job("reconcile", job)
        shared_job = build_job(config, acc, shared, interactive=False)
        assert shared_job["library_zone"] == "SharedSync-ABC"
        auth_job = build_job(config, acc, None, interactive=True)
        assert "target_dir" not in auth_job and auth_job["interactive"] is True
        validate_job("auth", auth_job)
        validate_job("list", auth_job)
        with pytest.raises(ValueError):
            validate_job("sync", auth_job)  # sync needs target_dir


def test_policy_from_settings_and_type_check(config: Config, store: Store) -> None:
    settings = Settings(store)
    settings.set("file_match_policy", "name-size-dedup-with-suffix")
    settings.set("threads_per_worker", "4")
    with store.session() as s:
        acc = repo.create_account(
            s, display_name="x", apple_id="b@icloud.com", region="com",
            password_enc=b"x", client_id="22222222-2222-2222-2222-222222222222",
        )
        policy = settings.policy()
        assert policy["file_match_policy"] == "name-size-dedup-with-suffix"
        assert policy["threads_num"] == 4
        job = build_job(config, acc, None, interactive=True, policy=policy)
        assert job["policy"] == policy
        validate_job("auth", job)
        broken = dict(job, until_found="50")
        with pytest.raises(ValueError, match="until_found"):
            validate_job("auth", broken)
        with pytest.raises(ValueError):
            validate_job("auth", dict(job, policy={"threads_num": "4"}))
        assert json.dumps(job)  # serialisable


def test_validate_job_rejects_bool_for_optional_int(config: Config, store: Store) -> None:
    with store.session() as s:
        acc = repo.create_account(
            s, display_name="x", apple_id="c@icloud.com", region="cn",
            password_enc=b"x", client_id="33333333-3333-3333-3333-333333333333",
        )
        job = build_job(config, acc, None, interactive=True)
    with pytest.raises(ValueError, match="recent"):
        validate_job("auth", dict(job, recent=True))
