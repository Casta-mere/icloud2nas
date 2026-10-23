import pytest

from icloud2nas.orchestrator.config import Config
from icloud2nas.orchestrator.store import repo
from icloud2nas.orchestrator.store.db import Store, make_engine, upgrade_db
from icloud2nas.orchestrator.store.models import Account


@pytest.fixture
def store(config: Config) -> Store:
    engine = make_engine(config.db_path)
    upgrade_db(engine)
    return Store(engine)


def _account(s: object, apple_id: str = "a@icloud.com") -> Account:
    return repo.create_account(
        s,  # type: ignore[arg-type]
        display_name="旭刚",
        apple_id=apple_id,
        region="cn",
        password_enc=b"enc",
        client_id="11111111-1111-1111-1111-111111111111",
    )


def test_account_round_trip(store: Store) -> None:
    with store.session() as s:
        acc = _account(s)
        assert acc.status == "new" and len(acc.id) == 32
        assert acc.created_at.tzinfo is not None
    with store.session() as s:
        again = repo.get_account_by_apple_id(s, "a@icloud.com")
        assert again is not None and again.display_name == "旭刚"
        assert repo.get_account(s, "nope") is None
        assert [a.apple_id for a in repo.list_accounts(s)] == ["a@icloud.com"]


def test_apple_id_is_unique(store: Store) -> None:
    with store.session() as s:
        _account(s)
    with pytest.raises(Exception, match="UNIQUE|unique"), store.session() as s:
        _account(s)


@pytest.mark.parametrize(
    ("existing", "candidate", "ok"),
    [
        ("旭刚", "旭刚2", True),
        ("旭刚", "旭刚/2024", False),
        ("旭刚/2024", "旭刚", False),
        ("旭刚", "旭刚", False),
        ("shared/家庭", "shared", False),
        ("a", "b/a", True),
    ],
)
def test_target_dir_nesting_rule(store: Store, existing: str, candidate: str, ok: bool) -> None:
    with store.session() as s:
        acc = _account(s)
        kind = "shared" if existing.startswith("shared/") else "personal"
        repo.upsert_library(
            s, acc, kind=kind, zone_name="PrimarySync", display_name="x",
            target_dir=existing, interval_min=30, nightly_at="03:00",
        )
        if ok:
            assert repo.validate_target_dir(s, candidate) == candidate
        else:
            with pytest.raises(ValueError):
                repo.validate_target_dir(s, candidate)


@pytest.mark.parametrize(
    "bad",
    [
        "/abs", "../x", "a/../../b", ".trash/x", "kept", ".hidden", "", "a/",
        "a/.icloud2nas-deleted/b-2026-10-23", "a/.icloud2nas/kept",
    ],
)
def test_target_dir_rejects_unsafe_values(store: Store, bad: str) -> None:
    with store.session() as s:
        if bad == "a/":
            assert repo.validate_target_dir(s, bad) == "a"  # trailing slash is normalised
            return
        with pytest.raises(ValueError):
            repo.validate_target_dir(s, bad)


def test_upsert_library_is_idempotent_and_exclude_self(store: Store) -> None:
    with store.session() as s:
        acc = _account(s)
        lib = repo.upsert_library(
            s, acc, kind="personal", zone_name="PrimarySync", display_name="旭刚",
            target_dir="旭刚", interval_min=30, nightly_at="03:00",
        )
        same = repo.upsert_library(
            s, acc, kind="personal", zone_name="PrimarySync", display_name="other",
            target_dir="elsewhere", interval_min=5, nightly_at="04:00",
        )
        assert same.id == lib.id and same.target_dir == "旭刚" and same.enabled is False
        assert repo.validate_target_dir(s, "旭刚", exclude_library_id=lib.id) == "旭刚"
        assert lib.stats == {} and lib.status == "ok"


def test_runs_plans_settings_and_sweep(store: Store) -> None:
    with store.session() as s:
        acc = _account(s)
        lib = repo.upsert_library(
            s, acc, kind="personal", zone_name="PrimarySync", display_name="旭刚",
            target_dir="旭刚", interval_min=30, nightly_at="03:00",
        )
        run = repo.create_run(
            s, account_id=acc.id, library_id=lib.id, mode="sync", trigger="manual"
        )
        assert run.status == "queued" and run.stats == {}
        run.status = "running"
        repo.create_run(
            s, account_id=acc.id, library_id=None, mode="auth", trigger="onboarding"
        )
        plan = repo.create_plan(
            s,
            library_id=lib.id,
            run_id=run.id,
            extraneous_count=7,
            plan_file="/data/plans/r.txt",
        )
        assert plan.status == "pending"
        assert [p.id for p in repo.list_plans(s)] == [plan.id]
        assert repo.get_setting(s, "max_workers", "2") == "2"
        repo.set_setting(s, "max_workers", "3")
        assert repo.get_setting(s, "max_workers", "2") == "3"
    with store.session() as s:
        assert repo.sweep_stale_runs(s, "daemon restarted") == 2
        for r in repo.list_runs(s):
            assert (
                r.status == "failed"
                and r.error_kind == "killed"
                and r.ended_at is not None
            )
        assert repo.sweep_stale_runs(s, "again") == 0
        assert [r.mode for r in repo.list_runs(s, library_id=lib.id)] == ["sync"]


def test_datetimes_are_aware_utc_after_reload(store: Store) -> None:
    from datetime import UTC, datetime, timedelta

    with store.session() as s:
        acc = _account(s)
        acc.backoff_until = datetime.now(UTC) + timedelta(minutes=5)
        account_id = acc.id
    with store.session() as s:
        again = repo.get_account(s, account_id)
        assert again is not None
        assert again.created_at.tzinfo is UTC and again.backoff_until is not None
        assert again.backoff_until.tzinfo is UTC
        assert again.backoff_until > datetime.now(UTC)  # comparison with aware now must work
    # Test naive datetime rejection in a new session
    with pytest.raises(Exception, match="naive"), store.session() as s:
        acc2 = repo.get_account(s, account_id)
        assert acc2 is not None
        acc2.backoff_until = datetime(2026, 1, 1)
        s.flush()
