# mypy: ignore-errors
"""Issue #4: accounts.session_expires_at, accounts.session_expiry_estimated.

Revision ID: 0004
Revises: 0003

Plain ALTER TABLE ADD/DROP COLUMN, never a batch rebuild: the app runs with foreign keys on,
libraries and runs reference accounts, so a rebuild's DROP TABLE accounts fails (the 0.2.0
outage). That failed attempt left an empty _alembic_tmp_accounts behind; drop it first.
"""

import sqlalchemy as sa
from alembic import op

from icloud2nas.orchestrator.store.models import UTCDateTime

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP TABLE IF EXISTS _alembic_tmp_accounts")
    op.add_column("accounts", sa.Column("session_expires_at", UTCDateTime, nullable=True))
    op.add_column(
        "accounts",
        sa.Column(
            "session_expiry_estimated",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column("accounts", "session_expiry_estimated")
    op.drop_column("accounts", "session_expires_at")
