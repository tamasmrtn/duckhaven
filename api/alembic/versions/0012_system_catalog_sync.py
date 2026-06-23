"""Add the system-catalog materializer cursor table

A singleton ``system_catalog_sync`` row tracking the ``(started_at, id)``
high-water mark of the last query copied into the Iceberg system catalog.

Revision ID: 0012
Revises: 0011
Create Date: 2026-06-23
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "system_catalog_sync",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("query_cursor_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("query_cursor_id", UUID(as_uuid=True), nullable=True),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(2048), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("system_catalog_sync")
