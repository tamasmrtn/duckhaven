"""Index the foreign keys that point at agents

Revision ID: 0070
Revises: 0069
Create Date: 2026-11-20

Deleting an agent makes Postgres find every row that refers to it, once per
deleted agent, and none of these columns had an index: each lookup read its
whole table. On a 521k-row ``queries`` table, deleting 750 agents took more than
two minutes and held row locks throughout. The agent handshake also deletes an
agent's old session credentials by ``credentials.agent_id`` on every first
registration.

The columns are mostly null (a credential belongs to a user or to an agent, and
most runs never ask for a particular agent), so the indexes are partial on
``IS NOT NULL``: the lookups compare with ``=``, which a null never satisfies,
and rows without an agent cost nothing to write. Building them reads each table
once; no rows change.

Also drops ``ix_credentials_token``, which 0001 created alongside the unique
constraint on the same column. The constraint's own index serves every lookup.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0070"
down_revision: str | None = "0069"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FOREIGN_KEYS = (
    ("credentials", "agent_id"),
    ("workspaces", "default_agent_id"),
    ("queries", "requested_agent_id"),
    ("saved_queries", "default_agent_id"),
    ("table_metadata", "last_write_agent_id"),
    ("schedules", "agent_id"),
    ("sql_sessions", "agent_id"),
    ("sql_sessions", "requested_agent_id"),
    ("worksheets", "agent_id"),
    ("result_cache_entries", "agent_id"),
)


def _name(table: str, column: str) -> str:
    return f"ix_{table}_{column}"


def upgrade() -> None:
    for table, column in FOREIGN_KEYS:
        where = sa.text(f"{column} IS NOT NULL")
        op.create_index(
            _name(table, column), table, [column], postgresql_where=where, sqlite_where=where
        )
    op.drop_index("ix_credentials_token", table_name="credentials")


def downgrade() -> None:
    op.create_index("ix_credentials_token", "credentials", ["token"])
    for table, column in reversed(FOREIGN_KEYS):
        op.drop_index(_name(table, column), table_name=table)
