"""Load and persist conversation history in DuckHaven's Postgres.

Message history is the SDK's own JSON serialization (``new_messages_json``), stored
one row per turn. Postgres stays the single state-of-record — no third-party
session store, no bespoke message schema.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Collection

from pydantic_ai import ModelMessagesTypeAdapter
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    UserPromptPart,
    sanitize_messages,
)
from pydantic_ai.usage import RunUsage
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.assistant import AssistantConversation, AssistantMessage, AssistantToolCall
from api.services.assistant.deps import ToolCallRecord


async def load_history(
    db: AsyncSession,
    conversation_id: uuid.UUID,
    resolved_tool_call_ids: Collection[str] = (),
) -> list[ModelMessage]:
    """Rebuild the full message history for a conversation, oldest turn first.

    ``resolved_tool_call_ids`` names the deferred tool calls being resumed (e.g. a
    write the user just approved). ``sanitize_messages`` strips a *trailing*
    unresolved tool call by default — which is exactly the paused approval we need
    to keep — so those ids must be whitelisted when resuming.
    """
    rows = (
        (
            await db.execute(
                select(AssistantMessage)
                .where(AssistantMessage.conversation_id == conversation_id)
                .order_by(AssistantMessage.ordinal)
            )
        )
        .scalars()
        .all()
    )
    combined: list = []
    for row in rows:
        combined.extend(row.payload)
    if not combined:
        return []
    messages = ModelMessagesTypeAdapter.validate_python(combined)
    # Defensive: history is server-owned, but strip any client-supplied system
    # prompts / unexpected parts before feeding it back to the model.
    return sanitize_messages(messages, resolved_tool_call_ids=resolved_tool_call_ids)


def render_transcript(messages: list[ModelMessage]) -> list[dict]:
    """Flatten message history into displayable {role, text} lines.

    User prompts and assistant text are surfaced; system prompts and tool
    request/return parts are omitted (tool activity is shown via the audit rows).
    """
    items: list[dict] = []
    for message in messages:
        if isinstance(message, ModelRequest):
            for part in message.parts:
                if isinstance(part, UserPromptPart) and isinstance(part.content, str):
                    items.append({"role": "user", "text": part.content})
        elif isinstance(message, ModelResponse):
            text = "".join(part.content for part in message.parts if isinstance(part, TextPart))
            if text.strip():
                items.append({"role": "assistant", "text": text})
    return items


async def _next_ordinal(db: AsyncSession, conversation_id: uuid.UUID) -> int:
    current = (
        await db.execute(
            select(func.max(AssistantMessage.ordinal)).where(
                AssistantMessage.conversation_id == conversation_id
            )
        )
    ).scalar_one_or_none()
    return 0 if current is None else current + 1


async def save_turn(
    db: AsyncSession,
    conversation: AssistantConversation,
    *,
    new_messages_json: bytes,
    usage: RunUsage,
    records: dict[str, ToolCallRecord],
) -> AssistantMessage:
    """Persist one turn: its messages, usage counters, and tool-call audit rows."""
    ordinal = await _next_ordinal(db, conversation.id)
    payload = json.loads(new_messages_json)
    input_tokens = usage.input_tokens or 0
    output_tokens = usage.output_tokens or 0
    message = AssistantMessage(
        conversation_id=conversation.id,
        ordinal=ordinal,
        payload=payload,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )
    db.add(message)
    conversation.total_input_tokens += input_tokens
    conversation.total_output_tokens += output_tokens

    for record in records.values():
        db.add(
            AssistantToolCall(
                conversation_id=conversation.id,
                tool=record.tool,
                args=record.args,
                status=record.status,
                detail=record.detail,
                query_id=uuid.UUID(record.query_id) if record.query_id else None,
                latency_ms=record.latency_ms,
            )
        )
    await db.commit()
    return message
