# mypy: ignore-errors
"""Plan 2b: accounts.primary_dir, libraries.first_full_at, notifications.

Revision ID: 0002
Revises: 0001
"""

import sqlalchemy as sa
from alembic import op

from icloud2nas.orchestrator.store.models import UTCDateTime

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("accounts") as batch:
        batch.add_column(sa.Column("primary_dir", sa.String(500), nullable=True))
    with op.batch_alter_table("libraries") as batch:
        batch.add_column(sa.Column("first_full_at", UTCDateTime, nullable=True))
    op.create_table(
        "notifications",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("event", sa.String(40), nullable=False),
        sa.Column("subject_key", sa.String(100), nullable=False),
        sa.Column("sent_at", UTCDateTime, nullable=False),
    )
    op.create_index(
        "ix_notifications_event_subject_key", "notifications", ["event", "subject_key"]
    )


def downgrade() -> None:
    op.drop_index("ix_notifications_event_subject_key", table_name="notifications")
    op.drop_table("notifications")
    with op.batch_alter_table("libraries") as batch:
        batch.drop_column("first_full_at")
    with op.batch_alter_table("accounts") as batch:
        batch.drop_column("primary_dir")
