"""SQL sessions

Revision ID: 0019
Revises: 0018
Create Date: 2026-07-13

Adds the SQL session layer's state-of-record (I9):

* ``sql_sessions`` — one persistent, agent-held DuckDB connection the control
  plane brokers for a client (dbt/dlt). Pins one ``agent_id`` (every statement
  routes there), records the requesting ``user_id`` (per-statement authorization),
  the ``active_catalog``, the scoped ``staging_uri`` a load may ``COPY`` to/from,
  and the lifecycle timestamps the idle-reaper uses (``last_active_at`` for the
  idle timeout, ``created_at`` for the max-lifetime cap).
* ``queries.session_id`` — statements run inside a session are ordinary
  ``queries`` rows tagged with their session (``origin="session"``), so the
  existing poll/fetch/audit pipeline is reused. Nullable; ``SET NULL`` on session
  delete so statement audit history survives.

All additive — no data migration.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sql_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="opening"),
        sa.Column("active_catalog", sa.String(255), nullable=True),
        sa.Column("staging_uri", sa.String(2048), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "last_active_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"]),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    # The reaper scans open sessions ordered by inactivity.
    op.create_index(
        "ix_sql_sessions_status_last_active",
        "sql_sessions",
        ["status", "last_active_at"],
    )

    # batch_alter_table so the FK add is portable: Postgres emits a plain ALTER,
    # SQLite uses the copy-and-move strategy (it cannot ALTER a named constraint).
    with op.batch_alter_table("queries") as batch_op:
        batch_op.add_column(sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=True))
        batch_op.create_foreign_key(
            "fk_queries_session_id",
            "sql_sessions",
            ["session_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_index("ix_queries_session_id", ["session_id"])


def downgrade() -> None:
    with op.batch_alter_table("queries") as batch_op:
        batch_op.drop_index("ix_queries_session_id")
        batch_op.drop_constraint("fk_queries_session_id", type_="foreignkey")
        batch_op.drop_column("session_id")
    op.drop_index("ix_sql_sessions_status_last_active", table_name="sql_sessions")
    op.drop_table("sql_sessions")
