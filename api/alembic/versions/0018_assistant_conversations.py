"""AI data assistant conversations, messages, and tool-call audit

Revision ID: 0018
Revises: 0017
Create Date: 2026-07-05

Adds the three tables backing the governed AI data assistant:

* ``assistant_conversations`` — one chat thread, scoped to a workspace, owned by
  the human ``user_id`` and attributed to the bound ``service_account_id`` whose
  grants govern the assistant's data access in that workspace.
* ``assistant_messages`` — one row per turn, holding the SDK's own JSON
  serialization of the message history so Postgres stays the single
  state-of-record (no third-party session store).
* ``assistant_tool_calls`` — the audit trail of every tool the assistant invoked
  (what it tried, with what args, and the outcome). Enforcement lives in the REST
  chokepoint; this table is audit/observability only.

All additive — no data migration.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "assistant_conversations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("service_account_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("title", sa.String(255), nullable=False, server_default="New conversation"),
        sa.Column("total_input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["service_account_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_assistant_conversations_ws_user",
        "assistant_conversations",
        ["workspace_id", "user_id"],
    )
    op.create_table(
        "assistant_messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column(
            "payload",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"], ["assistant_conversations.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("conversation_id", "ordinal", name="uq_assistant_messages_ordinal"),
    )
    op.create_table(
        "assistant_tool_calls",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tool", sa.String(100), nullable=False),
        sa.Column(
            "args",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
        ),
        sa.Column("status", sa.String(50), nullable=False, server_default="ok"),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("query_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"], ["assistant_conversations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["query_id"], ["queries.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_assistant_tool_calls_conversation",
        "assistant_tool_calls",
        ["conversation_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_assistant_tool_calls_conversation", table_name="assistant_tool_calls")
    op.drop_table("assistant_tool_calls")
    op.drop_table("assistant_messages")
    op.drop_index("ix_assistant_conversations_ws_user", table_name="assistant_conversations")
    op.drop_table("assistant_conversations")
