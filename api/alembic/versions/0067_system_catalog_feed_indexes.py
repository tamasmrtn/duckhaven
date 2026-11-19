"""Index the tables the system catalog copies by time

Revision ID: 0067
Revises: 0066
Create Date: 2026-11-19

The system catalog copies agent lifecycle events, agent minutes, SQL sessions and
table health samples in order of the time each row records, every few seconds, and
re-reads the last 15 minutes once a minute. No index leads with those columns, so
without these each read scans the whole table. Building them reads each table once;
no rows change.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0067"
down_revision: str | None = "0066"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (index, table, columns): each table's time, then its key.
INDEXES = (
    ("ix_agent_lifecycle_event_at", "agent_lifecycle_event", ["at", "id"]),
    ("ix_agent_metrics_minute_minute", "agent_metrics_minute", ["minute", "agent_id"]),
    ("ix_sql_sessions_created_at", "sql_sessions", ["created_at", "id"]),
    ("ix_table_health_sample_scanned_at", "table_health_sample", ["scanned_at", "id"]),
)


def upgrade() -> None:
    for name, table, columns in INDEXES:
        op.create_index(name, table, columns)


def downgrade() -> None:
    for name, table, _ in reversed(INDEXES):
        op.drop_index(name, table_name=table)
