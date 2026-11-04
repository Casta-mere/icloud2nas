# mypy: ignore-errors
"""Issue #4: accounts.session_expires_at, accounts.session_expiry_estimated.

Revision ID: 0004
Revises: 0003
"""

import sqlalchemy as sa
from alembic import op

from icloud2nas.orchestrator.store.models import UTCDateTime

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("accounts") as batch:
        batch.add_column(sa.Column("session_expires_at", UTCDateTime, nullable=True))
        batch.add_column(
            sa.Column(
                "session_expiry_estimated",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("accounts") as batch:
        batch.drop_column("session_expiry_estimated")
        batch.drop_column("session_expires_at")
