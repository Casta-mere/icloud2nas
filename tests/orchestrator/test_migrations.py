"""Alembic migrations: fresh DBs reach head; Plan 2a DBs are stamped 0001 then upgraded."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import inspect

from icloud2nas.orchestrator.store.db import MIGRATIONS_DIR, make_engine, upgrade_db
from icloud2nas.orchestrator.store.models import Base

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
    assert upgrade_db(engine) == "0003"
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
    assert upgrade_db(engine) == "0003"
    assert "primary_dir" in {c["name"] for c in inspect(engine).get_columns("accounts")}
    with engine.connect() as c:
        assert c.exec_driver_sql("SELECT apple_id FROM accounts").scalar_one() == "a@x.com"
    assert upgrade_db(engine) == "0003"  # idempotent


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
            " x'00', 'c1', 'ok', 0, '2026-01-01 00:00:00', '旭刚')"
        )
    assert not inspect(engine).has_table("deleted_dirs")
    assert upgrade_db(engine) == "0003"
    assert inspect(engine).has_table("deleted_dirs")
    cols = {c["name"]: c for c in inspect(engine).get_columns("accounts")}
    assert cols["owner_uid"]["nullable"] and cols["owner_gid"]["nullable"]
    with engine.connect() as c:
        row = c.exec_driver_sql(
            "SELECT apple_id, primary_dir, owner_uid, owner_gid FROM accounts"
        ).one()
    assert tuple(row) == ("a@x.com", "旭刚", None, None)


def test_models_match_head(tmp_path: Path) -> None:
    engine = make_engine(tmp_path / "x.db")
    upgrade_db(engine)
    with engine.connect() as c:
        diffs = compare_metadata(MigrationContext.configure(c), Base.metadata)
    assert diffs == []
