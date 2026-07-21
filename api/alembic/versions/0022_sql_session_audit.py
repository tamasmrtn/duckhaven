"""SQL session audit fields

Revision ID: 0022
Revises: 0021
Create Date: 2026-07-21

Makes a finished session's *story* readable, not just its final status.

* ``close_reason`` — why the session ended, as a typed value. The taxonomy already
  existed: ``record_sql_session_closed(reason)`` meters ``client`` / ``idle`` /
  ``max_lifetime`` / ``open_timeout`` / ``agent_disconnect`` / ``agent_lease`` /
  ``failed`` at every terminal path. Until now it went only to Prometheus, and the
  row kept the reason as free text in ``error`` (``"reaped (idle)"``,
  ``"agent disconnected"``) — which an audit UI would have had to string-parse.
* ``client_name`` / ``client_version`` — which tool opened the session, parsed from
  the ``User-Agent`` the connector already sends (``dbt-duckhaven/1.2.0``). The
  analog of Postgres' ``application_name`` and Databricks'
  ``system.query.history.client_application``; the 64-char name budget follows
  ``application_name``'s ``NAMEDATALEN``.

Also indexes ``(workspace_id, created_at)`` for the workspace session list.
``queries.session_id`` is already indexed (0019), so the statement timeline and the
per-session statement count need nothing here.

All nullable and additive — no data migration. Sessions that ended before this
release keep a null ``close_reason``; the UI reports those as unknown rather than
guessing from ``error``.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0022"
down_revision: str | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("sql_sessions", sa.Column("close_reason", sa.String(32), nullable=True))
    op.add_column("sql_sessions", sa.Column("client_name", sa.String(64), nullable=True))
    op.add_column("sql_sessions", sa.Column("client_version", sa.String(32), nullable=True))
    op.create_index(
        "ix_sql_sessions_workspace_created",
        "sql_sessions",
        ["workspace_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_sql_sessions_workspace_created", table_name="sql_sessions")
    op.drop_column("sql_sessions", "client_version")
    op.drop_column("sql_sessions", "client_name")
    op.drop_column("sql_sessions", "close_reason")
