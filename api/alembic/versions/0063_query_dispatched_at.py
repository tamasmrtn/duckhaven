"""Query dispatched_at

Revision ID: 0063
Revises: 0062
Create Date: 2026-11-15

``queries.dispatched_at`` records when a run was sent to its agent. An agent
answers a run on the connection it arrived on, so a run sent before that
connection closed can never be answered. The presence sweeper compares this
time with the agent's connection events and fails such runs, which otherwise
stayed queued or running for good.

The column is nullable and left empty on existing rows: a run sent before the
upgrade is not judged by a time it never recorded.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0063"
down_revision: str | None = "0062"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("queries", sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("queries", "dispatched_at")
