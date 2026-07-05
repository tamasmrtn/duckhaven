import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ConversationCreate(BaseModel):
    # Optional opening message; when present the created conversation is returned
    # and the client immediately opens the streaming turn endpoint.
    title: str | None = None


class ConversationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    workspace_id: uuid.UUID
    title: str
    total_input_tokens: int
    total_output_tokens: int
    created_at: datetime
    updated_at: datetime


class ToolCallOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tool: str
    args: dict[str, Any] | None
    status: str
    detail: str | None
    query_id: uuid.UUID | None
    latency_ms: int | None
    created_at: datetime


class TranscriptItem(BaseModel):
    """One rendered line of the conversation for display."""

    role: str  # "user" | "assistant"
    text: str


class ConversationDetailOut(ConversationOut):
    transcript: list[TranscriptItem]
    tool_calls: list[ToolCallOut]


class TurnRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=16_000)
    # Active catalog slug for unqualified table names; defaults to the workspace default.
    catalog: str | None = None
    # Current worksheet-editor SQL, so the assistant can read and propose edits to it.
    editor_sql: str | None = Field(default=None, max_length=100_000)


class ApprovalRequest(BaseModel):
    tool_call_id: str
    approved: bool
    # Optional message shown to the model when denying.
    reason: str | None = None
    catalog: str | None = None
