"""Drop lineage backfill state and its history index

Revision ID: 0034
Revises: 0033
Create Date: 2026-08-16

The lineage backfill — replaying a workspace's query history through lineage
extraction — has been withdrawn, so the two things 0033 added to support it go
with it: the ``lineage_backfills`` state row, and ``ix_queries_workspace_started``,
which existed to make the walk over history resumable in ``(started_at, id)``
order. ``queries`` keeps its workspace index, which is what the UI's recent-query
listing reads.

Deliberately narrow. 0033's other change — ``table_metadata.table_uuid`` and
``ix_table_metadata_uuid`` — is how a renamed table is told from a dropped one
recreated under the same name, has nothing to do with the backfill, and is
untouched here. No lineage row is read or rewritten: the graph itself, including
every edge a backfill happened to have recorded, is left exactly as it stands.
Those edges were derived by the same extraction the live path uses, so there is
nothing to unwind.

``downgrade()`` restores both objects as 0033 created them.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0034"
down_revision: str | None = "0033"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index("ix_queries_workspace_started", table_name="queries")
    op.drop_table("lineage_backfills")


def downgrade() -> None:
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

    op.create_index(
        "ix_queries_workspace_started",
        "queries",
        ["workspace_id", "started_at", "id"],
    )
