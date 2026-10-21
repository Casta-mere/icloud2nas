# mypy: ignore-errors
"""Alembic environment, programmatic use only: the caller passes the connection."""

from alembic import context

from icloud2nas.orchestrator.store.models import Base

config = context.config
connection = config.attributes["connection"]

context.configure(
    connection=connection,
    target_metadata=Base.metadata,
    render_as_batch=True,
)
with context.begin_transaction():
    context.run_migrations()
