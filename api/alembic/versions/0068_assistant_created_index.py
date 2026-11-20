"""Index assistant turns and tool calls by when they were written

Revision ID: 0068
Revises: 0067
Create Date: 2026-11-19

The system catalog copies each assistant turn's token counts and each tool call
in ``(created_at, id)`` order, every few seconds, and re-reads the last 15 minutes
once a minute. No index leads with ``created_at``, so without these each read
scans the whole table. Building them reads each table once; no rows change.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0068"
down_revision: str | None = "0067"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INDEXES = (
    ("ix_assistant_messages_created_at", "assistant_messages"),
    ("ix_assistant_tool_calls_created_at", "assistant_tool_calls"),
)


def upgrade() -> None:
    for name, table in INDEXES:
        op.create_index(name, table, ["created_at", "id"])


def downgrade() -> None:
    for name, table in reversed(INDEXES):
        op.drop_index(name, table_name=table)
