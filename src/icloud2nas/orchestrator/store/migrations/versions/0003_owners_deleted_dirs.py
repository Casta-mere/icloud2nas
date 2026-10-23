# mypy: ignore-errors
"""Plan 3: accounts.owner_uid/owner_gid, deleted_dirs.

Revision ID: 0003
Revises: 0002
"""

import sqlalchemy as sa
from alembic import op

from icloud2nas.orchestrator.store.models import UTCDateTime

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("accounts") as batch:
        batch.add_column(sa.Column("owner_uid", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("owner_gid", sa.Integer(), nullable=True))
    op.create_table(
        "deleted_dirs",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("path", sa.String(1000), nullable=False),
        sa.Column("deleted_at", UTCDateTime, nullable=False),
    )


def downgrade() -> None:
    op.drop_table("deleted_dirs")
    with op.batch_alter_table("accounts") as batch:
        batch.drop_column("owner_gid")
        batch.drop_column("owner_uid")
