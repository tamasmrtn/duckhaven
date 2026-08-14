"""Lineage graph

Revision ID: 0032
Revises: 0031
Create Date: 2026-08-14

DuckHaven knew which tables a statement touched only for the duration of the
grant check that parsed it — the relationship was recomputed and thrown away on
every dispatch. These two tables make it durable: ``lineage_edges`` holds one
``source -> target`` dataset relationship per producer, and
``lineage_column_edges`` is created empty, ready for the column-level refinement
that lands later, so that work needs no further migration.

Both endpoints are denormalized onto the edge rather than pointing at an asset
table, matching how ``catalog_grants`` and ``table_metadata`` key by name: Polaris
owns catalog structure and the control plane must not cache it (I3). The
``*_key`` columns are the canonical asset key that traversal joins on; they are
built on ``catalogs.id`` so renaming a catalog does not orphan its lineage.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0032"
down_revision: str | None = "0031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "lineage_edges",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("source_key", sa.String(1024), nullable=False),
        sa.Column(
            "source_catalog_id",
            UUID(as_uuid=True),
            sa.ForeignKey("catalogs.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("source_system", sa.String(128), nullable=True),
        sa.Column("source_schema", sa.String(255), nullable=False),
        sa.Column("source_table", sa.String(255), nullable=False),
        sa.Column("target_key", sa.String(1024), nullable=False),
        sa.Column(
            "target_catalog_id",
            UUID(as_uuid=True),
            sa.ForeignKey("catalogs.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("target_system", sa.String(128), nullable=True),
        sa.Column("target_schema", sa.String(255), nullable=False),
        sa.Column("target_table", sa.String(255), nullable=False),
        sa.Column("provider", sa.String(50), nullable=False),
        sa.Column("provider_run_id", sa.String(255), nullable=True),
        sa.Column(
            "workspace_id",
            UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("operation", sa.String(32), nullable=True),
        sa.Column("confidence", sa.String(16), nullable=False, server_default="exact"),
        sa.Column("last_query_id", UUID(as_uuid=True), nullable=True),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("observation_count", sa.Integer(), nullable=False, server_default="1"),
        sa.UniqueConstraint(
            "provider", "source_key", "target_key", name="uq_lineage_edges_identity"
        ),
    )
    op.create_index("ix_lineage_edges_source_key", "lineage_edges", ["source_key"])
    op.create_index("ix_lineage_edges_target_key", "lineage_edges", ["target_key"])
    op.create_index(
        "ix_lineage_edges_provider_run", "lineage_edges", ["provider", "provider_run_id"]
    )
    op.create_index(
        "ix_lineage_edges_source_catalog",
        "lineage_edges",
        ["source_catalog_id", "source_schema", "source_table"],
    )
    op.create_index(
        "ix_lineage_edges_target_catalog",
        "lineage_edges",
        ["target_catalog_id", "target_schema", "target_table"],
    )

    op.create_table(
        "lineage_column_edges",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "edge_id",
            UUID(as_uuid=True),
            sa.ForeignKey("lineage_edges.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source_column", sa.String(255), nullable=False),
        sa.Column("target_column", sa.String(255), nullable=False),
        sa.UniqueConstraint(
            "edge_id", "source_column", "target_column", name="uq_lineage_column_edges_identity"
        ),
    )
    op.create_index("ix_lineage_column_edges_edge", "lineage_column_edges", ["edge_id"])


def downgrade() -> None:
    op.drop_index("ix_lineage_column_edges_edge", table_name="lineage_column_edges")
    op.drop_table("lineage_column_edges")
    op.drop_index("ix_lineage_edges_target_catalog", table_name="lineage_edges")
    op.drop_index("ix_lineage_edges_source_catalog", table_name="lineage_edges")
    op.drop_index("ix_lineage_edges_provider_run", table_name="lineage_edges")
    op.drop_index("ix_lineage_edges_target_key", table_name="lineage_edges")
    op.drop_index("ix_lineage_edges_source_key", table_name="lineage_edges")
    op.drop_table("lineage_edges")
