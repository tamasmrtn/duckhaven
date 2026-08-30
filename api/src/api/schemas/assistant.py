import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class AssistantStatusOut(BaseModel):
    # Whether the assistant is enabled in this deployment. Reported even when
    # disabled so the UI can show a clear "turned off" state.
    enabled: bool
    # Whether the assistant can actually be used here, and if not, why. The account
    # is created with no access at all, so a fresh deployment reports
    # "no_workspace_access" until an admin grants some. Reported so the UI can name
    # the fix up front, rather than letting a turn spend a model run discovering
    # every tool call is denied. Always "disabled" when the feature is off.
    availability: Literal["disabled", "account_unavailable", "no_workspace_access", "ok"] = (
        "disabled"
    )


class ConversationCreate(BaseModel):
    # Optional opening message; when present the created conversation is returned
    # and the client immediately opens the streaming turn endpoint.
    title: str | None = None


class ConversationUpdate(BaseModel):
    title: str = Field(min_length=1, max_length=255)


class ConversationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    workspace_id: uuid.UUID
    title: str
    total_input_tokens: int
    total_output_tokens: int
    created_at: datetime
    updated_at: datetime


class TableRefOut(BaseModel):
    catalog: str
    schema_name: str
    table: str


class ToolCallOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tool: str
    args: dict[str, Any] | None
    status: str
    detail: str | None
    query_id: uuid.UUID | None
    latency_ms: int | None
    tables: list[TableRefOut] | None
    created_at: datetime


class TranscriptItem(BaseModel):
    """One rendered line of the conversation for display."""

    role: str  # "user" | "assistant"
    text: str
    # The SQL this turn ran or proposed, if any — attributed by a same-transaction
    # timestamp window (see render_transcript_with_sql), shown inline by default.
    sql: str | None = None


class ConversationDetailOut(ConversationOut):
    transcript: list[TranscriptItem]
    tool_calls: list[ToolCallOut]
    # Whether this conversation has more turns than are replayed to the model
    # (assistant_history_turn_cap) — the UI surfaces this so users know the
    # oldest messages are no longer part of the assistant's context.
    history_truncated: bool


class TurnRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=16_000)
    # Active catalog slug for unqualified table names; defaults to the workspace default.
    catalog: str | None = None
    # Current worksheet-editor SQL, so the assistant can read and propose edits to it.
    editor_sql: str | None = Field(default=None, max_length=100_000)
    # The user's current worksheet text selection, if non-empty, so a proposed edit
    # can be scoped to just that fragment instead of rewriting the whole worksheet.
    selection_sql: str | None = Field(default=None, max_length=100_000)

    @field_validator("selection_sql")
    @classmethod
    def _blank_selection_is_none(cls, v: str | None) -> str | None:
        # Normalize an empty/whitespace-only selection to None at the boundary, so
        # "scoped" (runner: `is not None`) and the selection tool (truthiness) can't
        # disagree about whether the user actually selected anything.
        return None if v is not None and not v.strip() else v


class ApprovalRequest(BaseModel):
    tool_call_id: str
    approved: bool
    # Optional message shown to the model when denying.
    reason: str | None = None
    catalog: str | None = None
