"""Add lakehouse maintenance tables

Revision ID: 0009
Revises: 0008
Create Date: 2026-06-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "maintenance_policy",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("scan_enabled", sa.Boolean(), nullable=False),
        sa.Column("scan_frequency", sa.String(length=20), nullable=False),
        sa.Column("preset", sa.String(length=20), nullable=False),
        sa.Column("thresholds", postgresql.JSONB(), nullable=False),
        sa.Column("max_tables_per_cycle", sa.Integer(), nullable=False),
        sa.Column("last_scan_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_deep_scan_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("scan_cursor", sa.String(length=512), nullable=True),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "table_health_sample",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("schema_name", sa.String(length=255), nullable=False),
        sa.Column("table_name", sa.String(length=255), nullable=False),
        sa.Column("scanned_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("snapshot_count", sa.Integer(), nullable=True),
        sa.Column("data_file_count", sa.Integer(), nullable=True),
        sa.Column("manifest_count", sa.Integer(), nullable=True),
        sa.Column("total_data_bytes", sa.BigInteger(), nullable=True),
        sa.Column("avg_file_bytes", sa.BigInteger(), nullable=True),
        sa.Column("metadata_bytes", sa.BigInteger(), nullable=True),
        sa.Column("orphan_bytes", sa.BigInteger(), nullable=True),
        sa.Column("orphan_file_count", sa.Integer(), nullable=True),
        sa.Column("small_file_ratio", sa.Float(), nullable=True),
        sa.Column("score", sa.Integer(), nullable=True),
        sa.Column("factors", postgresql.JSONB(), nullable=True),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_table_health_sample_ident_time",
        "table_health_sample",
        ["workspace_id", "schema_name", "table_name", "scanned_at"],
    )

    op.create_table(
        "maintenance_recommendation",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("schema_name", sa.String(length=255), nullable=False),
        sa.Column("table_name", sa.String(length=255), nullable=False),
        sa.Column("kind", sa.String(length=40), nullable=False),
        sa.Column("severity", sa.String(length=20), nullable=False),
        sa.Column("confidence", sa.String(length=20), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("estimated_impact", postgresql.JSONB(), nullable=True),
        sa.Column("remediation", postgresql.JSONB(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "schema_name",
            "table_name",
            "kind",
            name="uq_maintenance_recommendation_ident_kind",
        ),
    )


def downgrade() -> None:
    op.drop_table("maintenance_recommendation")
    op.drop_index("ix_table_health_sample_ident_time", table_name="table_health_sample")
    op.drop_table("table_health_sample")
    op.drop_table("maintenance_policy")
