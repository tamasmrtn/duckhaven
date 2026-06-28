"""Agent replica ownership for HA dispatch

Revision ID: 0013
Revises: 0012
Create Date: 2026-06-28

Adds nullable ``owner_id`` / ``owner_url`` columns to ``agents``. They record
which API replica currently holds an agent's WebSocket and the internal URL peers
use to forward dispatch frames to it, enabling a query created on any replica to
reach an agent connected to another. Both are NULL when the agent is not
connected.

Backward-compat: the columns are nullable and additive; a single-replica deploy
simply sets them to its own identity and forwards to itself.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("agents", sa.Column("owner_id", sa.String(255), nullable=True))
    op.add_column("agents", sa.Column("owner_url", sa.String(255), nullable=True))


def downgrade() -> None:
    op.drop_column("agents", "owner_url")
    op.drop_column("agents", "owner_id")
