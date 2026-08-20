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

from api.config import settings
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
    # Coarse guard against unbounded context: only the most recent N turns are
    # replayed. Fetch the newest N (ordinal desc), then restore chronological order.
    rows = (
        (
            await db.execute(
                select(AssistantMessage)
                .where(AssistantMessage.conversation_id == conversation_id)
                .order_by(AssistantMessage.ordinal.desc())
                .limit(settings.assistant_history_turn_cap)
            )
        )
        .scalars()
        .all()
    )
    combined: list = []
    for row in reversed(rows):
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


async def render_transcript_with_sql(db: AsyncSession, conversation_id: uuid.UUID) -> list[dict]:
    """Like ``render_transcript``, but attach the SQL each turn ran (or proposed)
    to its assistant line, so the UI can show it inline without a separate lookup.

    ``AssistantToolCall`` rows have no FK to the ``AssistantMessage`` (turn) that
    produced them. But ``save_turn`` writes a turn's message row and all of that
    turn's tool-call rows in one transaction/commit, so a tool call can be
    attributed to the turn whose time window it falls in: a message at ordinal N
    owns tool calls with ``created_at`` in ``(row[N-1].created_at, row[N].created_at]``
    (unbounded below for the first row). This is a heuristic derived from that
    same-commit invariant, not a real key — it holds as long as turns don't
    complete within the same timestamp tick, which is not a concern at Python's
    datetime resolution.
    """
    rows = (
        (
            await db.execute(
                select(AssistantMessage)
                .where(AssistantMessage.conversation_id == conversation_id)
                .order_by(AssistantMessage.ordinal.desc())
                .limit(settings.assistant_history_turn_cap)
            )
        )
        .scalars()
        .all()
    )
    rows = list(reversed(rows))
    if not rows:
        return []
    # Bound the tool-call scan to the displayed window. Without the lower bound,
    # the oldest *visible* turn would absorb the tool calls of every *dropped*
    # older turn (its window is unbounded below), misattributing a dropped turn's
    # SQL onto the oldest visible answer once the conversation exceeds the cap.
    tool_calls = (
        (
            await db.execute(
                select(AssistantToolCall)
                .where(AssistantToolCall.conversation_id == conversation_id)
                .where(AssistantToolCall.created_at >= rows[0].created_at)
                .order_by(AssistantToolCall.created_at)
            )
        )
        .scalars()
        .all()
    )
    items: list[dict] = []
    lower_bound = None
    # Both ``rows`` and ``tool_calls`` are sorted ascending, so walk them together
    # with a single forward pointer (O(rows + calls)) rather than re-scanning the
    # whole tool-call list per row.
    call_idx = 0
    for row in rows:
        upper_bound = row.created_at
        sql: str | None = None
        while call_idx < len(tool_calls):
            call = tool_calls[call_idx]
            if lower_bound is not None and call.created_at <= lower_bound:
                call_idx += 1
                continue
            if call.created_at > upper_bound:
                break
            if call.tool in ("run_sql", "propose_sql_edit") and isinstance(call.args, dict):
                candidate = call.args.get("sql")
                if candidate:
                    # Last matching call wins: a turn that both ran and proposed
                    # SQL surfaces whichever committed later (its most recent action).
                    sql = candidate
            call_idx += 1
        row_messages = sanitize_messages(ModelMessagesTypeAdapter.validate_python(row.payload))
        row_items = render_transcript(row_messages)
        for item in row_items:
            item["sql"] = None
        if sql:
            for item in reversed(row_items):
                if item["role"] == "assistant":
                    item["sql"] = sql
                    break
        items.extend(row_items)
        lower_bound = upper_bound
    return items


async def is_history_truncated(db: AsyncSession, conversation_id: uuid.UUID) -> bool:
    """Whether this conversation has more turns than the history replay cap.

    One ``AssistantMessage`` row is one full turn, so a plain count against
    ``assistant_history_turn_cap`` tells the UI whether the oldest turns have
    already fallen out of what's replayed to the model (see ``load_history``).
    """
    total = (
        await db.execute(
            select(func.count())
            .select_from(AssistantMessage)
            .where(AssistantMessage.conversation_id == conversation_id)
        )
    ).scalar_one()
    return total > settings.assistant_history_turn_cap


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
                tables=record.tables,
            )
        )
    await db.commit()
    return message
