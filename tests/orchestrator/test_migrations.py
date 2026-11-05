"""Alembic migrations: fresh DBs reach head; Plan 2a DBs are stamped 0001 then upgraded."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, inspect

from icloud2nas.orchestrator.store.db import MIGRATIONS_DIR, Store, make_engine, upgrade_db
from icloud2nas.orchestrator.store.models import Account, Base

PLAN2A_ACCOUNTS_DDL = """\
CREATE TABLE accounts (
	id VARCHAR(32) NOT NULL,
	display_name VARCHAR(200) NOT NULL,
	apple_id VARCHAR(320) NOT NULL,
	region VARCHAR(3) NOT NULL,
	password_enc BLOB NOT NULL,
	client_id VARCHAR(36) NOT NULL,
	status VARCHAR(20) NOT NULL,
	last_auth_ok_at DATETIME,
	last_error TEXT,
	failures INTEGER NOT NULL,
	backoff_until DATETIME,
	created_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (apple_id)
)
"""

PLAN2A_SETTINGS_DDL = """\
CREATE TABLE settings (
	"key" VARCHAR(100) NOT NULL,
	value TEXT NOT NULL,
	PRIMARY KEY ("key")
)
"""

PLAN2A_LIBRARIES_DDL = """\
CREATE TABLE libraries (
	id VARCHAR(32) NOT NULL,
	account_id VARCHAR(32) NOT NULL,
	kind VARCHAR(10) NOT NULL,
	zone_name VARCHAR(100) NOT NULL,
	display_name VARCHAR(200) NOT NULL,
	target_dir VARCHAR(500) NOT NULL,
	enabled BOOLEAN NOT NULL,
	status VARCHAR(20) NOT NULL,
	interval_min INTEGER NOT NULL,
	nightly_at VARCHAR(5) NOT NULL,
	last_run_at DATETIME,
	last_success_at DATETIME,
	last_nightly_on DATE,
	stats_json TEXT NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (account_id, zone_name),
	FOREIGN KEY(account_id) REFERENCES accounts (id)
)
"""

PLAN2A_RUNS_DDL = """\
CREATE TABLE runs (
	id VARCHAR(32) NOT NULL,
	account_id VARCHAR(32) NOT NULL,
	library_id VARCHAR(32),
	mode VARCHAR(10) NOT NULL,
	"trigger" VARCHAR(12) NOT NULL,
	status VARCHAR(12) NOT NULL,
	started_at DATETIME NOT NULL,
	ended_at DATETIME,
	exit_code INTEGER,
	stats_json TEXT NOT NULL,
	error_kind VARCHAR(20),
	error_message TEXT,
	log_path VARCHAR(500),
	plan_file VARCHAR(500),
	PRIMARY KEY (id),
	FOREIGN KEY(account_id) REFERENCES accounts (id),
	FOREIGN KEY(library_id) REFERENCES libraries (id)
)
"""

PLAN2A_RECONCILE_PLANS_DDL = """\
CREATE TABLE reconcile_plans (
	id VARCHAR(32) NOT NULL,
	library_id VARCHAR(32) NOT NULL,
	run_id VARCHAR(32) NOT NULL,
	created_at DATETIME NOT NULL,
	extraneous_count INTEGER NOT NULL,
	plan_file VARCHAR(500) NOT NULL,
	status VARCHAR(10) NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(library_id) REFERENCES libraries (id),
	FOREIGN KEY(run_id) REFERENCES runs (id)
)
"""


def test_fresh_db_is_upgraded_to_head(tmp_path: Path) -> None:
    engine = make_engine(tmp_path / "x.db")
    assert upgrade_db(engine) == "0004"
    assert inspect(engine).has_table("notifications")
    assert "first_full_at" in {c["name"] for c in inspect(engine).get_columns("libraries")}


def test_plan2a_db_is_stamped_then_migrated(tmp_path: Path) -> None:
    engine = make_engine(tmp_path / "x.db")
    with engine.begin() as c:
        for ddl in (
            PLAN2A_ACCOUNTS_DDL,
            PLAN2A_SETTINGS_DDL,
            PLAN2A_LIBRARIES_DDL,
            PLAN2A_RUNS_DDL,
            PLAN2A_RECONCILE_PLANS_DDL,
        ):
            c.exec_driver_sql(ddl)
        c.exec_driver_sql(
            "INSERT INTO accounts (id, display_name, apple_id, region, password_enc, client_id,"
            " status, failures, created_at) VALUES ('a1', 'A', 'a@x.com', 'cn', x'00', 'c1',"
            " 'ok', 0, '2026-01-01 00:00:00')"
        )
    assert upgrade_db(engine) == "0004"
    assert "primary_dir" in {c["name"] for c in inspect(engine).get_columns("accounts")}
    with engine.connect() as c:
        assert c.exec_driver_sql("SELECT apple_id FROM accounts").scalar_one() == "a@x.com"
    assert upgrade_db(engine) == "0004"  # idempotent


def test_migration_0003_upgrades_plan2c_db(tmp_path: Path) -> None:
    engine = make_engine(tmp_path / "x.db")
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    with engine.begin() as connection:
        cfg.attributes["connection"] = connection
        command.upgrade(cfg, "0002")  # a Plan 2c database
    with engine.begin() as c:
        c.exec_driver_sql(
            "INSERT INTO accounts (id, display_name, apple_id, region, password_enc, client_id,"
            " status, failures, created_at, primary_dir) VALUES ('a1', 'A', 'a@x.com', 'cn',"
            " x'00', 'c1', 'ok', 0, '2026-01-01 00:00:00', '小明')"
        )
    assert not inspect(engine).has_table("deleted_dirs")
    assert upgrade_db(engine) == "0004"
    assert inspect(engine).has_table("deleted_dirs")
    cols = {c["name"]: c for c in inspect(engine).get_columns("accounts")}
    assert cols["owner_uid"]["nullable"] and cols["owner_gid"]["nullable"]
    with engine.connect() as c:
        row = c.exec_driver_sql(
            "SELECT apple_id, primary_dir, owner_uid, owner_gid FROM accounts"
        ).one()
    assert tuple(row) == ("a@x.com", "小明", None, None)


def test_models_match_head(tmp_path: Path) -> None:
    engine = make_engine(tmp_path / "x.db")
    upgrade_db(engine)
    with engine.connect() as c:
        diffs = compare_metadata(MigrationContext.configure(c), Base.metadata)
    assert diffs == []


def test_migration_0004_adds_session_expiry_and_downgrades(tmp_path: Path) -> None:
    engine = make_engine(tmp_path / "x.db")
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    with engine.begin() as connection:
        cfg.attributes["connection"] = connection
        command.upgrade(cfg, "0003")
    with engine.begin() as c:
        c.exec_driver_sql(
            "INSERT INTO accounts (id, display_name, apple_id, region, password_enc, client_id,"
            " status, failures, created_at) VALUES ('a1', 'A', 'a@x.com', 'cn', x'00', 'c1',"
            " 'ok', 0, '2026-01-01 00:00:00')"
        )
    assert upgrade_db(engine) == "0004"
    cols = {c["name"]: c for c in inspect(engine).get_columns("accounts")}
    assert cols["session_expires_at"]["nullable"]
    assert not cols["session_expiry_estimated"]["nullable"]
    with engine.connect() as c:
        row = c.exec_driver_sql(
            "SELECT apple_id, session_expires_at, session_expiry_estimated FROM accounts"
        ).one()
    assert tuple(row) == ("a@x.com", None, 0)  # existing accounts: expiry unknown
    with engine.begin() as connection:
        cfg.attributes["connection"] = connection
        command.downgrade(cfg, "0003")
    names = {c["name"] for c in inspect(engine).get_columns("accounts")}
    assert "session_expires_at" not in names and "session_expiry_estimated" not in names
    with engine.connect() as c:
        assert c.exec_driver_sql("SELECT apple_id FROM accounts").scalar_one() == "a@x.com"


# What 0.2.0's 0004 left behind on the NAS: it rebuilt accounts (move and copy), created this,
# then failed to DROP the referenced accounts table; the copy rolled back, the CREATE did not.
LEFTOVER_TMP_ACCOUNTS_DDL = """\
CREATE TABLE _alembic_tmp_accounts (
	id VARCHAR(32) NOT NULL,
	display_name VARCHAR(200) NOT NULL,
	apple_id VARCHAR(320) NOT NULL,
	region VARCHAR(3) NOT NULL,
	password_enc BLOB NOT NULL,
	client_id VARCHAR(36) NOT NULL,
	status VARCHAR(20) NOT NULL,
	last_auth_ok_at DATETIME,
	last_error TEXT,
	failures INTEGER NOT NULL,
	backoff_until DATETIME,
	created_at DATETIME NOT NULL,
	primary_dir VARCHAR(500),
	owner_uid INTEGER,
	owner_gid INTEGER,
	session_expires_at DATETIME,
	session_expiry_estimated BOOLEAN DEFAULT 0 NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (apple_id)
)
"""


def _alembic(engine: Engine, action: Callable[[Config, str], None], revision: str) -> None:
    """Run an alembic command over a connection opened the way the app opens it."""
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    with engine.begin() as connection:
        assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
        cfg.attributes["connection"] = connection
        action(cfg, revision)


def _referenced_db(tmp_path: Path, revision: str) -> Engine:
    """A DB at ``revision`` whose account is referenced by a library, a run and a plan."""
    engine = make_engine(tmp_path / "x.db")
    _alembic(engine, command.upgrade, revision)
    with engine.begin() as c:
        c.exec_driver_sql(
            "INSERT INTO accounts (id, display_name, apple_id, region, password_enc, client_id,"
            " status, failures, created_at) VALUES ('a1', 'A', 'a@x.com', 'cn', x'00', 'c1',"
            " 'ok', 0, '2026-01-01 00:00:00')"
        )
        c.exec_driver_sql(
            "INSERT INTO libraries (id, account_id, kind, zone_name, display_name, target_dir,"
            " enabled, status, interval_min, nightly_at, stats_json) VALUES ('l1', 'a1',"
            " 'personal', 'PrimarySync', 'P', 'p', 1, 'ok', 30, '03:00', '{}')"
        )
        c.exec_driver_sql(
            'INSERT INTO runs (id, account_id, library_id, mode, "trigger", status, started_at,'
            " stats_json) VALUES ('r1', 'a1', 'l1', 'sync', 'manual', 'ok',"
            " '2026-01-01 00:00:00', '{}')"
        )
        c.exec_driver_sql(
            "INSERT INTO reconcile_plans (id, library_id, run_id, created_at, extraneous_count,"
            " plan_file, status) VALUES ('p1', 'l1', 'r1', '2026-01-01 00:00:00', 0, 'p.json',"
            " 'pending')"
        )
    return engine


def _assert_referenced_rows_intact(engine: Engine) -> None:
    with engine.connect() as c:
        assert c.exec_driver_sql("SELECT id, apple_id FROM accounts").all() == [("a1", "a@x.com")]
        assert c.exec_driver_sql("SELECT id, account_id FROM libraries").all() == [("l1", "a1")]
        runs = c.exec_driver_sql("SELECT id, account_id, library_id FROM runs").all()
        assert runs == [("r1", "a1", "l1")]
        plans = c.exec_driver_sql("SELECT id, library_id, run_id FROM reconcile_plans").all()
        assert plans == [("p1", "l1", "r1")]
        assert c.exec_driver_sql("PRAGMA foreign_key_check").all() == []
        assert c.exec_driver_sql("PRAGMA integrity_check").scalar_one() == "ok"
        tables = inspect(c).get_table_names()
    assert [t for t in tables if t.startswith("_alembic_tmp_")] == []


def _version(engine: Engine) -> str:
    with engine.connect() as c:
        version: str = c.exec_driver_sql("SELECT version_num FROM alembic_version").scalar_one()
    return version


def test_migration_0004_upgrades_db_whose_account_is_referenced(tmp_path: Path) -> None:
    # The 0.2.0 outage: rebuilding accounts meant dropping a table libraries and runs reference.
    engine = _referenced_db(tmp_path, "0003")
    assert upgrade_db(engine) == "0004"
    _assert_referenced_rows_intact(engine)
    with engine.connect() as c:
        cols = {r[1]: tuple(r[2:5]) for r in c.exec_driver_sql("PRAGMA table_info(accounts)")}
        row = c.exec_driver_sql(
            "SELECT session_expires_at, session_expiry_estimated,"
            " typeof(session_expiry_estimated) FROM accounts"
        ).one()
    # (type, notnull, default) as 0.2.0's rebuild left them on DBs that did reach 0004
    assert cols["session_expires_at"] == ("DATETIME", 0, None)
    assert cols["session_expiry_estimated"] == ("BOOLEAN", 1, "0")
    assert tuple(row) == (None, 0, "integer")
    with Store(engine).session() as s:
        account = s.get(Account, "a1")
        assert account is not None
        assert account.session_expires_at is None and account.session_expiry_estimated is False


def test_migration_0004_drops_the_leftover_of_the_failed_020_attempt(tmp_path: Path) -> None:
    engine = _referenced_db(tmp_path, "0003")
    with engine.begin() as c:
        c.exec_driver_sql(LEFTOVER_TMP_ACCOUNTS_DDL)
    assert upgrade_db(engine) == "0004"
    _assert_referenced_rows_intact(engine)  # includes: no _alembic_tmp_* table left


def test_migration_0004_downgrades_db_whose_account_is_referenced(tmp_path: Path) -> None:
    engine = _referenced_db(tmp_path, "0003")
    assert upgrade_db(engine) == "0004"
    _alembic(engine, command.downgrade, "0003")
    assert _version(engine) == "0003"
    names = {c["name"] for c in inspect(engine).get_columns("accounts")}
    assert not names & {"session_expires_at", "session_expiry_estimated"}
    _assert_referenced_rows_intact(engine)


def test_every_migration_steps_up_and_down_with_referenced_rows(tmp_path: Path) -> None:
    """Foreign keys are on, so a migration that rebuilds a referenced table fails here."""
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    revisions = [s.revision for s in ScriptDirectory.from_config(cfg).walk_revisions()][::-1]
    engine = _referenced_db(tmp_path, revisions[0])
    for revision in revisions[1:]:
        _alembic(engine, command.upgrade, revision)
        assert _version(engine) == revision
        _assert_referenced_rows_intact(engine)
    for revision in revisions[-2::-1]:
        _alembic(engine, command.downgrade, revision)
        assert _version(engine) == revision
        _assert_referenced_rows_intact(engine)
    assert upgrade_db(engine) == revisions[-1]
    _assert_referenced_rows_intact(engine)
