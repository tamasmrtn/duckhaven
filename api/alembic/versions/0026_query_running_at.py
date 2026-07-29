"""Record when a query started running

Revision ID: 0026
Revises: 0025
Create Date: 2026-07-28

Adds ``queries.running_at`` — the moment the agent admitted a run and began
executing it. ``started_at`` is when the row was written, i.e. submission, so the
two together split a run's wall-clock into the time it waited in the agent's
admission queue and the time it actually ran. That split is what the query history
table shows on hover; without it a slow run is indistinguishable from a queued one.

The transition is already observable — ``services.query.handle_agent_frame`` records
the ``duckhaven_query_queue_wait_seconds`` histogram at exactly this point — so this
only makes it durable.

Also adds ``ix_queries_agent_finished``. Every chart on the per-agent monitoring page
range-scans ``queries`` by agent and finish time, and the table carried only
``ix_queries_workspace_id`` and ``ix_queries_status``.

Both additive; the column is nullable, so existing rows simply report no split and
render exactly as before.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0026"
down_revision: str | None = "0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("queries", sa.Column("running_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_queries_agent_finished", "queries", ["agent_id", "finished_at"])


def downgrade() -> None:
    op.drop_index("ix_queries_agent_finished", table_name="queries")
    op.drop_column("queries", "running_at")
