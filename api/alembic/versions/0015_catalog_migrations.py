"""Catalog storage backend migrations

Revision ID: 0015
Revises: 0014
Create Date: 2026-06-30

Adds the tables backing seamless catalog storage-backend migration: a
``catalog_migrations`` run record (state machine + progress), a
``catalog_migration_tables`` per-table checkpoint for crash-resume, and a
``catalog_migration_events`` user-facing log stream. All three are new and
additive — existing catalogs/queries are unaffected, and ``catalogs`` itself is
unchanged (migration state is derived from these tables, not denormalized onto
the catalog row).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "catalog_migrations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("catalog_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_storage_backend_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("target_storage_backend_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("shadow_polaris_name", sa.String(255), nullable=True),
        sa.Column("source_polaris_name", sa.String(255), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("tables_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tables_done", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("bytes_total", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("bytes_copied", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cutover_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["catalog_id"], ["catalogs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_storage_backend_id"], ["storage_backends.id"]),
        sa.ForeignKeyConstraint(["target_storage_backend_id"], ["storage_backends.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    # Drives the freeze gate ("does this catalog have an active migration?") and
    # the active-migration uniqueness check on start.
    op.create_index("ix_catalog_migrations_active", "catalog_migrations", ["catalog_id", "status"])

    op.create_table(
        "catalog_migration_tables",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("migration_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("schema_name", sa.String(255), nullable=False),
        sa.Column("table_name", sa.String(255), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("source_metadata_location", sa.String(2048), nullable=True),
        sa.Column("target_metadata_location", sa.String(2048), nullable=True),
        sa.Column("bytes_copied", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["migration_id"], ["catalog_migrations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_catalog_migration_tables_migration_id",
        "catalog_migration_tables",
        ["migration_id"],
    )

    op.create_table(
        "catalog_migration_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("migration_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("seq", sa.BigInteger(), nullable=False),
        sa.Column("level", sa.String(10), nullable=False, server_default="info"),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["migration_id"], ["catalog_migrations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_catalog_migration_events_seq",
        "catalog_migration_events",
        ["migration_id", "seq"],
    )


def downgrade() -> None:
    op.drop_index("ix_catalog_migration_events_seq", table_name="catalog_migration_events")
    op.drop_table("catalog_migration_events")
    op.drop_index("ix_catalog_migration_tables_migration_id", table_name="catalog_migration_tables")
    op.drop_table("catalog_migration_tables")
    op.drop_index("ix_catalog_migrations_active", table_name="catalog_migrations")
    op.drop_table("catalog_migrations")
