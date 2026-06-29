"""Scheduled / recurring jobs

Revision ID: 0014
Revises: 0013
Create Date: 2026-06-29

Adds the generic ``schedules`` table: a cron-triggered, leader-elected unit of
unattended work. ``job_type`` discriminates what runs; v1 implements only
``"saved_query"`` (run a saved query's SQL on a cron cadence). Each dispatched run
is recorded as a ``queries`` row tagged ``origin="scheduled"`` and linked back via
the new nullable ``queries.schedule_id`` column, which powers the per-schedule run
history. Both additions are nullable/additive — interactive queries are unaffected.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "schedules",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_type", sa.String(50), nullable=False, server_default="saved_query"),
        sa.Column("saved_query_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("agent_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("cron", sa.String(255), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        # Soft pointer (no FK) to the most recent run, to avoid a circular
        # schedules<->queries dependency.
        sa.Column("last_run_query_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"]),
        sa.ForeignKeyConstraint(["saved_query_id"], ["saved_queries.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_schedules_due", "schedules", ["enabled", "next_run_at"])

    op.add_column("queries", sa.Column("schedule_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        "fk_queries_schedule_id",
        "queries",
        "schedules",
        ["schedule_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_queries_schedule_id", "queries", ["schedule_id"])


def downgrade() -> None:
    op.drop_index("ix_queries_schedule_id", table_name="queries")
    op.drop_constraint("fk_queries_schedule_id", "queries", type_="foreignkey")
    op.drop_column("queries", "schedule_id")
    op.drop_index("ix_schedules_due", table_name="schedules")
    op.drop_table("schedules")
