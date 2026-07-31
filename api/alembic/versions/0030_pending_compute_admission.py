"""Pending compute admission for sessions and targeted runs

Revision ID: 0030
Revises: 0029
Create Date: 2026-07-31

Lets a SQL session and an explicitly-targeted run be *parked* while the compute
they need starts, instead of being rejected with 503. Until now only a pool query
could park (``queries.agent_id IS NULL`` + ``origin="elastic"``); every other
admission path required an already-connected agent, so a cold pool failed every
session client and every run naming an idle-terminated agent.

``requested_agent_id`` records the agent the caller *named*, kept deliberately
separate from ``agent_id``, which stays "the agent that actually holds/ran this".
That separation is what lets the binder keep its claim guard at ``agent_id IS
NULL`` -- the same atomic claim ``bind_queued_work`` already uses, which is safe
across replicas because the WHERE clause, not a read, is the lock. Reusing
``agent_id`` for the request would make "parked for agent A" and "dispatched to
agent A, awaiting ack" indistinguishable, and the binder would re-dispatch live
work. ``ON DELETE SET NULL`` mirrors the existing ``sql_sessions.agent_id``, so
deleting an agent strands nothing.

``sql_sessions.opening_at`` exists because the session reaper measures its
``opening`` deadline from ``created_at``. A session that waited out a cold start
would be born ~250s "old" and get reaped on the first tick after it finally
started opening. Anchoring the deadline to ``COALESCE(opening_at, created_at)``
is identical for every pre-existing row and for the warm path, where the two are
written microseconds apart.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0030"
down_revision: str | None = "0029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # batch_alter_table so the FK-adding ALTERs also run on SQLite, as 0019 does.
    with op.batch_alter_table("sql_sessions") as batch_op:
        batch_op.add_column(
            sa.Column("requested_agent_id", postgresql.UUID(as_uuid=True), nullable=True)
        )
        batch_op.add_column(sa.Column("opening_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.create_foreign_key(
            "fk_sql_sessions_requested_agent_id",
            "agents",
            ["requested_agent_id"],
            ["id"],
            ondelete="SET NULL",
        )
    # The binder's lookup: parked sessions only, which is a handful of rows during a
    # cold start and none at all the rest of the time.
    op.create_index(
        "ix_sql_sessions_pending",
        "sql_sessions",
        ["requested_agent_id"],
        postgresql_where=sa.text("status = 'pending'"),
        sqlite_where=sa.text("status = 'pending'"),
    )
    with op.batch_alter_table("queries") as batch_op:
        batch_op.add_column(
            sa.Column("requested_agent_id", postgresql.UUID(as_uuid=True), nullable=True)
        )
        batch_op.create_foreign_key(
            "fk_queries_requested_agent_id",
            "agents",
            ["requested_agent_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("queries") as batch_op:
        batch_op.drop_constraint("fk_queries_requested_agent_id", type_="foreignkey")
        batch_op.drop_column("requested_agent_id")
    op.drop_index("ix_sql_sessions_pending", table_name="sql_sessions")
    with op.batch_alter_table("sql_sessions") as batch_op:
        batch_op.drop_constraint("fk_sql_sessions_requested_agent_id", type_="foreignkey")
        batch_op.drop_column("opening_at")
        batch_op.drop_column("requested_agent_id")
