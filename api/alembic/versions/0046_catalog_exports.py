"""Exporting a DuckLake catalog to Iceberg

Revision ID: 0046
Revises: 0045
Create Date: 2026-09-21

Records runs of copying a DuckLake catalog into Iceberg. Separate from
``catalog_migrations`` because an active migration freezes writes to the
source, and an export must not.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0046"
down_revision: str | None = "0045"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "catalog_exports",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "source_catalog_id",
            sa.Uuid(),
            sa.ForeignKey("catalogs.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("target_catalog_id", sa.Uuid(), sa.ForeignKey("catalogs.id"), nullable=True),
        sa.Column("target_name", sa.String(255), nullable=False),
        sa.Column(
            "target_storage_backend_id",
            sa.Uuid(),
            sa.ForeignKey("storage_backends.id"),
            nullable=False,
        ),
        sa.Column("workspace_id", sa.Uuid(), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("query_id", sa.Uuid(), nullable=True),
        sa.Column("tables_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tables_done", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "cancel_requested", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("catalog_exports")
