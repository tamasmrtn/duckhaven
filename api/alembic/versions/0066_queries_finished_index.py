"""Index finished queries by finish time

Revision ID: 0066
Revises: 0065
Create Date: 2026-11-17

The system catalog copies finished runs into ``query.history`` in
``(finished_at, id)`` order, every few seconds, and re-reads the last 15 minutes
once a minute. Without an index each of those reads scans every query ever run.

Partial on ``finished_at IS NOT NULL``: a queued or running run is never read by
it. Building it reads the table once; no rows change.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0066"
down_revision: str | None = "0065"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_WHERE = sa.text("finished_at IS NOT NULL")


def upgrade() -> None:
    op.create_index(
        "ix_queries_finished",
        "queries",
        ["finished_at", "id"],
        postgresql_where=_WHERE,
        sqlite_where=_WHERE,
    )


def downgrade() -> None:
    op.drop_index("ix_queries_finished", table_name="queries")
