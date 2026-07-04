"""Granular catalog/schema/table access grants

Revision ID: 0017
Revises: 0016
Create Date: 2026-07-03

Adds the ``catalog_grants`` table and the ``workspace_catalogs.access_mode``
column that together implement scoped, sub-catalog access control.

``access_mode`` defaults to ``"open"`` so every existing attachment keeps
today's behavior byte-for-byte (the workspace role governs every schema/table).
Only when an attachment is switched to ``"scoped"`` are ``catalog_grants`` rows
consulted, narrowing a principal's access down to the catalog, schema, or table
they were granted (tier ``metadata < reader < writer``). A grant is an ACL row
keyed by *name* (like ``table_metadata``), not a cache of catalog structure.

Both additions are additive/defaulted — no data migration is required.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "workspace_catalogs",
        sa.Column("access_mode", sa.String(20), nullable=False, server_default="open"),
    )
    op.create_table(
        "catalog_grants",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("catalog_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("schema_name", sa.String(255), nullable=True),
        sa.Column("table_name", sa.String(255), nullable=True),
        sa.Column("tier", sa.String(20), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["catalog_id"], ["catalogs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "table_name IS NULL OR schema_name IS NOT NULL",
            name="ck_catalog_grants_table_needs_schema",
        ),
    )
    # One grant per principal per node. COALESCE folds NULL name columns so the
    # uniqueness holds for catalog-/schema-/table-level rows alike.
    op.create_index(
        "uq_catalog_grants_node",
        "catalog_grants",
        [
            "user_id",
            "catalog_id",
            sa.text("coalesce(schema_name, '')"),
            sa.text("coalesce(table_name, '')"),
        ],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_catalog_grants_node", table_name="catalog_grants")
    op.drop_table("catalog_grants")
    op.drop_column("workspace_catalogs", "access_mode")
