"""Per-minute agent telemetry rollup

Revision ID: 0028
Revises: 0027
Create Date: 2026-07-28

Adds ``agent_metrics_minute``: one row per agent per minute of CPU, memory, queue
depth and held-session count.

Agents sample themselves every ~2s, but those samples only ever reached a 150-entry
in-memory ring buffer (``services.agent_registry``) — roughly five minutes, per API
replica, lost on restart. Nothing about an agent's utilization was durable, so the
1–24h windows the monitoring page offers could not be served at all.

Aggregating to the minute is the whole point: ~1.4k rows/agent/day, against the ~43k
that persisting raw 2s samples would cost, and still finer than the coarsest bucket
any window renders.

Each resource keeps both an average and a max because they answer different
questions — the average is what the agent cost you, the max is what made a query
slow. Queue depths keep only the max: a peak of one queued query is meaningful, a
mean of 0.3 is not.

``sample_count`` exists so a flush can *merge*. Only the replica owning an agent's
socket accumulates, but ownership can move mid-minute, and the second replica to
flush that minute must combine a weighted mean rather than overwrite the first
replica's partial minute.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0028"
down_revision: str | None = "0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_metrics_minute",
        sa.Column("agent_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("minute", sa.DateTime(timezone=True), nullable=False),
        sa.Column("cpu_avg", sa.Float(), nullable=False),
        sa.Column("cpu_max", sa.Float(), nullable=False),
        sa.Column("mem_avg", sa.Float(), nullable=False),
        sa.Column("mem_max", sa.Float(), nullable=False),
        sa.Column("running_max", sa.Integer(), nullable=False),
        sa.Column("queued_max", sa.Integer(), nullable=False),
        sa.Column("session_max", sa.Integer(), nullable=False),
        sa.Column("sample_count", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("agent_id", "minute"),
    )


def downgrade() -> None:
    op.drop_table("agent_metrics_minute")
