"""Per-minute agent rollup: coverage and OOM kills

Revision ID: 0050
Revises: 0049
Create Date: 2026-09-30

``covered_s`` is how many seconds of the minute the agent's samples actually span
(the sum of each sample's interval). Without it a minute the agent reported for ten
seconds before it died looked exactly like a full minute of the same readings, and
the monitoring page could not say how much of a bucket it measured.

``oom_kills`` is the number of processes the kernel's OOM killer took inside the
agent's cgroup during the minute, summed from the agent's per-interval deltas.

Both are nullable: a row written from an agent too old to report them has no value,
and the page must show that as "not measured" rather than as zero. ``mem_max`` keeps
its name but, from agents that report it, now holds the peak reached between samples
rather than the highest sampled level.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0050"
down_revision: str | None = "0049"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("agent_metrics_minute", sa.Column("covered_s", sa.Float(), nullable=True))
    op.add_column("agent_metrics_minute", sa.Column("oom_kills", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("agent_metrics_minute", "oom_kills")
    op.drop_column("agent_metrics_minute", "covered_s")
