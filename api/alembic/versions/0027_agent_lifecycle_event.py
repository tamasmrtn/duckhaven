"""Agent lifecycle event trail

Revision ID: 0027
Revises: 0026
Create Date: 2026-07-28

Adds ``agent_lifecycle_event``: an append-only record of every transition an agent
makes — ``provisioning``, ``connected``, ``disconnected``, ``terminating``,
``terminated``, ``failed``.

The ``agents`` row is mutated in place, and a restart reuses it, so it only ever
describes the agent *now*; every previous run's history was destroyed. This table is
the history, and the only possible source for the monitoring page's
running/not-running timeline.

``connected``/``disconnected`` are recorded for static agents too, so the timeline
means one thing for both kinds: the agent's socket was up and it could serve work.
Elastic agents additionally record the provisioning and teardown transitions
bracketing that.

``reason`` reuses the vocabulary the reaper already counts by (``idle``,
``max_lifetime``, ``provisioning_timeout``, ``orphan``, ``dead_row``) — those
counters are computed every cycle today and thrown away — so the page and the
``duckhaven_agent_reaped`` counter cannot drift apart. NULL when a transition needs
no explanation.

Rows cascade with the agent: a deleted agent has no monitoring page, and its query
history is preserved separately (``delete_agent`` nulls ``queries.agent_id``).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0027"
down_revision: str | None = "0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_lifecycle_event",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event", sa.String(32), nullable=False),
        sa.Column("reason", sa.String(64), nullable=True),
        sa.Column("at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_agent_lifecycle_event_agent_at", "agent_lifecycle_event", ["agent_id", "at"]
    )


def downgrade() -> None:
    op.drop_index("ix_agent_lifecycle_event_agent_at", table_name="agent_lifecycle_event")
    op.drop_table("agent_lifecycle_event")
