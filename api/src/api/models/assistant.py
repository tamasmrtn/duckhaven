from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from api.db.base import Base

# JSONB on Postgres, plain JSON on other dialects (SQLite under unit tests). Mirrors
# the pattern used by Query.profile / the maintenance models.
_Json = JSON().with_variant(JSONB, "postgresql")


class AssistantConversation(Base):
    """One chat thread between a human user and the governed assistant.

    Scoped to a workspace: the assistant's data access in this thread is whatever
    the bound service account (``service_account_id``) is granted in that
    workspace. ``user_id`` is the human who started the thread (audit + ownership).
    """

    __tablename__ = "assistant_conversations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    # The service account the assistant acted as. Nullable/SET NULL so deleting the
    # account never orphans a conversation's audit trail.
    service_account_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False, default="New conversation")
    # Running usage totals across all turns, for cost visibility.
    total_input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    messages: Mapped[list[AssistantMessage]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="AssistantMessage.ordinal",
    )


class AssistantMessage(Base):
    """One persisted turn: the Pydantic AI messages produced by a single run.

    ``payload`` is ``result.new_messages_json()`` — the SDK's own version-tolerant
    serialization of the ModelMessage list — so Postgres stays the single
    state-of-record with no third-party store or bespoke schema.
    """

    __tablename__ = "assistant_messages"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("assistant_conversations.id", ondelete="CASCADE"), nullable=False
    )
    # Monotonic per-conversation turn index; drives history ordering.
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    payload: Mapped[list] = mapped_column(_Json, nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    conversation: Mapped[AssistantConversation] = relationship(back_populates="messages")


class AssistantToolCall(Base):
    """Audit record for one tool invocation inside a turn.

    Written from the governance capability's hooks. The enforcement of what the
    tool may do lives server-side in the REST chokepoint; this row is the audit and
    observability trail (what the assistant tried, with what args, and the outcome).
    """

    __tablename__ = "assistant_tool_calls"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("assistant_conversations.id", ondelete="CASCADE"), nullable=False
    )
    tool: Mapped[str] = mapped_column(String(100), nullable=False)
    args: Mapped[dict | None] = mapped_column(_Json, nullable=True)
    # "ok" | "error" | "denied" | "approval_required"
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="ok")
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    # The real Query row a run_sql call produced, when applicable.
    query_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("queries.id", ondelete="SET NULL"), nullable=True
    )
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
