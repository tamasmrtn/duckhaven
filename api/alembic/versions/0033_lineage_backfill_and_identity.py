"""Lineage backfill state and stable table identity

Revision ID: 0033
Revises: 0032
Create Date: 2026-08-14

Three additive changes, all in service of a graph a user can act on without
double-checking it.

``lineage_backfills`` is the state of replaying a workspace's own query history
through lineage extraction — what was asked for, how far it got, what it found.
Durable rather than a request that runs to completion, because the work is
unbounded in principle and has to survive a restart.

``table_metadata.table_uuid`` records the Iceberg identity of a table. Lineage
keys on the *address* ``(catalog, schema, table)``, which is what makes traversal
a single indexed lookup, and a rename changes the address. The Iceberg id does
not, so recording it is what lets DuckHaven tell a renamed table from a dropped
one recreated under the same name — the difference between carrying lineage
across and correctly throwing it away. It is populated only from handlers that
already hold a table's Iceberg metadata, so it costs no extra catalog traffic
(I3 is intact: this is a recorded observation, not a structural cache, exactly
like ``snapshot_id`` beside it).

``ix_queries_workspace_started`` supports the backfill's forward walk over
history. ``queries`` is indexed by workspace alone today, which is enough for the
UI's "recent queries" but would turn a walk over months of statements into a
repeated sort of the whole partition.

Nothing existing is rewritten. Both new columns are nullable, no lineage key
changes, and rows written before this revision keep working untouched — they
simply do not participate in rename detection until their table's identity is
next observed.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0033"
down_revision: str | None = "0032"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "lineage_backfills",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "workspace_id",
            UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("dry_run", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("since_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("covered_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("covered_through", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cursor_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cursor_query_id", UUID(as_uuid=True), nullable=True),
        sa.Column("queries_scanned", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("queries_with_lineage", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("queries_skipped", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("parse_failures", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("queries_failed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("edges_created", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("edges_updated", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "requested_by",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("workspace_id", name="uq_lineage_backfills_workspace"),
    )

    op.add_column("table_metadata", sa.Column("table_uuid", sa.String(64), nullable=True))
    # Rename detection asks "does this catalog already know this Iceberg id, and
    # if so under what name" — a lookup by identity within a catalog.
    op.create_index(
        "ix_table_metadata_uuid",
        "table_metadata",
        ["catalog_id", "table_uuid"],
    )

    op.create_index(
        "ix_queries_workspace_started",
        "queries",
        ["workspace_id", "started_at", "id"],
    )


def downgrade() -> None:
    op.drop_index("ix_queries_workspace_started", table_name="queries")
    op.drop_index("ix_table_metadata_uuid", table_name="table_metadata")
    op.drop_column("table_metadata", "table_uuid")
    op.drop_table("lineage_backfills")
