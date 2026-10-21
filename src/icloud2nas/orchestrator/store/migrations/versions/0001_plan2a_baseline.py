# mypy: ignore-errors
"""Plan 2a baseline: accounts, settings, libraries, runs, reconcile_plans.

Revision ID: 0001
Revises:
"""

import sqlalchemy as sa
from alembic import op

from icloud2nas.orchestrator.store.models import UTCDateTime

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "accounts",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("display_name", sa.String(200), nullable=False),
        sa.Column("apple_id", sa.String(320), nullable=False),
        sa.Column("region", sa.String(3), nullable=False),
        sa.Column("password_enc", sa.LargeBinary, nullable=False),
        sa.Column("client_id", sa.String(36), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("last_auth_ok_at", UTCDateTime),
        sa.Column("last_error", sa.Text),
        sa.Column("failures", sa.Integer, nullable=False),
        sa.Column("backoff_until", UTCDateTime),
        sa.Column("created_at", UTCDateTime, nullable=False),
        sa.UniqueConstraint("apple_id"),
    )
    op.create_table(
        "settings",
        sa.Column("key", sa.String(100), primary_key=True),
        sa.Column("value", sa.Text, nullable=False),
    )
    op.create_table(
        "libraries",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("account_id", sa.String(32), sa.ForeignKey("accounts.id"), nullable=False),
        sa.Column("kind", sa.String(10), nullable=False),
        sa.Column("zone_name", sa.String(100), nullable=False),
        sa.Column("display_name", sa.String(200), nullable=False),
        sa.Column("target_dir", sa.String(500), nullable=False),
        sa.Column("enabled", sa.Boolean, nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("interval_min", sa.Integer, nullable=False),
        sa.Column("nightly_at", sa.String(5), nullable=False),
        sa.Column("last_run_at", UTCDateTime),
        sa.Column("last_success_at", UTCDateTime),
        sa.Column("last_nightly_on", sa.Date),
        sa.Column("stats_json", sa.Text, nullable=False),
        sa.UniqueConstraint("account_id", "zone_name"),
    )
    op.create_table(
        "runs",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("account_id", sa.String(32), sa.ForeignKey("accounts.id"), nullable=False),
        sa.Column("library_id", sa.String(32), sa.ForeignKey("libraries.id")),
        sa.Column("mode", sa.String(10), nullable=False),
        sa.Column("trigger", sa.String(12), nullable=False),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("started_at", UTCDateTime, nullable=False),
        sa.Column("ended_at", UTCDateTime),
        sa.Column("exit_code", sa.Integer),
        sa.Column("stats_json", sa.Text, nullable=False),
        sa.Column("error_kind", sa.String(20)),
        sa.Column("error_message", sa.Text),
        sa.Column("log_path", sa.String(500)),
        sa.Column("plan_file", sa.String(500)),
    )
    op.create_table(
        "reconcile_plans",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("library_id", sa.String(32), sa.ForeignKey("libraries.id"), nullable=False),
        sa.Column("run_id", sa.String(32), sa.ForeignKey("runs.id"), nullable=False),
        sa.Column("created_at", UTCDateTime, nullable=False),
        sa.Column("extraneous_count", sa.Integer, nullable=False),
        sa.Column("plan_file", sa.String(500), nullable=False),
        sa.Column("status", sa.String(10), nullable=False),
    )


def downgrade() -> None:
    for table in ("reconcile_plans", "runs", "libraries", "settings", "accounts"):
        op.drop_table(table)
