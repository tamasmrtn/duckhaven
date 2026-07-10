from datetime import UTC, datetime, timedelta

import pytest_asyncio
from conftest import seed_workspace
from pydantic_ai import ModelMessagesTypeAdapter
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    UserPromptPart,
)
from pydantic_ai.usage import RunUsage
from sqlalchemy import select

from api.models.assistant import AssistantConversation, AssistantMessage, AssistantToolCall
from api.models.user import User
from api.services.assistant.deps import ToolCallRecord
from api.services.assistant.persistence import (
    load_history,
    render_transcript,
    render_transcript_with_sql,
    save_turn,
)
from api.services.auth import hash_password


@pytest_asyncio.fixture
async def conversation(db_session) -> AssistantConversation:
    user = User(email="u@p.local", password_hash=hash_password("pw"), name="U", role="user")
    db_session.add(user)
    await db_session.commit()
    ws, _ = await seed_workspace(db_session, user_id=user.id)
    conv = AssistantConversation(workspace_id=ws.id, user_id=user.id, title="t")
    db_session.add(conv)
    await db_session.commit()
    await db_session.refresh(conv)
    return conv


def _turn_json(user_text: str, assistant_text: str) -> bytes:
    messages = [
        ModelRequest(parts=[UserPromptPart(content=user_text)]),
        ModelResponse(parts=[TextPart(content=assistant_text)]),
    ]
    return ModelMessagesTypeAdapter.dump_json(messages)


async def test_save_and_load_roundtrip(db_session, conversation):
    await save_turn(
        db_session,
        conversation,
        new_messages_json=_turn_json("hi", "hello"),
        usage=RunUsage(input_tokens=3, output_tokens=5),
        records={"c1": ToolCallRecord(tool="list_catalogs", args={}, status="ok", latency_ms=9)},
    )
    history = await load_history(db_session, conversation.id)
    transcript = render_transcript(history)
    assert transcript == [
        {"role": "user", "text": "hi"},
        {"role": "assistant", "text": "hello"},
    ]
    assert conversation.total_input_tokens == 3
    assert conversation.total_output_tokens == 5
    tool_calls = (await db_session.execute(select(AssistantToolCall))).scalars().all()
    assert len(tool_calls) == 1
    assert tool_calls[0].tool == "list_catalogs"
    assert tool_calls[0].status == "ok"


def _has_tool_call(messages, tool_call_id: str) -> bool:
    return any(
        isinstance(m, ModelResponse)
        and any(isinstance(p, ToolCallPart) and p.tool_call_id == tool_call_id for p in m.parts)
        for m in messages
    )


async def test_resumed_pending_tool_call_survives_sanitize(db_session, conversation):
    # A turn that ended awaiting approval: history ends with an unresolved tool call.
    messages = [
        ModelRequest(parts=[UserPromptPart(content="delete stuff")]),
        ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name="run_sql",
                    args={"sql": "DELETE FROM t"},
                    tool_call_id="call-xyz",
                )
            ]
        ),
    ]
    await save_turn(
        db_session,
        conversation,
        new_messages_json=ModelMessagesTypeAdapter.dump_json(messages),
        usage=RunUsage(input_tokens=1, output_tokens=1),
        records={},
    )
    # Without whitelisting, sanitize strips the trailing unresolved tool call.
    plain = await load_history(db_session, conversation.id)
    assert not _has_tool_call(plain, "call-xyz")
    # On an approval resume the id is whitelisted, so the pending call is kept.
    resumed = await load_history(db_session, conversation.id, resolved_tool_call_ids={"call-xyz"})
    assert _has_tool_call(resumed, "call-xyz")


async def test_load_history_caps_to_recent_turns(db_session, conversation, monkeypatch):
    from api.config import settings

    monkeypatch.setattr(settings, "assistant_history_turn_cap", 2)
    for i in range(3):
        await save_turn(
            db_session,
            conversation,
            new_messages_json=_turn_json(f"q{i}", f"a{i}"),
            usage=RunUsage(input_tokens=1, output_tokens=1),
            records={},
        )
    history = await load_history(db_session, conversation.id)
    texts = [item["text"] for item in render_transcript(history)]
    # Only the most recent 2 turns survive, in chronological order.
    assert texts == ["q1", "a1", "q2", "a2"]


async def test_render_transcript_with_sql_attributes_sql_to_the_right_turn(
    db_session, conversation
):
    await save_turn(
        db_session,
        conversation,
        new_messages_json=_turn_json("how many rows?", "There are 10."),
        usage=RunUsage(input_tokens=1, output_tokens=1),
        records={
            "c1": ToolCallRecord(
                tool="run_sql", args={"sql": "SELECT count(*) FROM t"}, status="ok"
            )
        },
    )
    await save_turn(
        db_session,
        conversation,
        new_messages_json=_turn_json("and the max?", "The max is 99."),
        usage=RunUsage(input_tokens=1, output_tokens=1),
        records={
            "c2": ToolCallRecord(tool="run_sql", args={"sql": "SELECT max(x) FROM t"}, status="ok")
        },
    )
    # SQLite's CURRENT_TIMESTAMP has whole-second resolution, so two turns saved
    # back-to-back in a fast test can land in the same tick (Postgres's real
    # microsecond resolution makes this a non-issue in production) — stagger the
    # rows' created_at explicitly so the windowing logic is exercised
    # deterministically rather than depending on wall-clock granularity.
    base = datetime(2026, 1, 1, tzinfo=UTC)
    messages = (
        (
            await db_session.execute(
                select(AssistantMessage).where(AssistantMessage.conversation_id == conversation.id)
            )
        )
        .scalars()
        .all()
    )
    for m in messages:
        m.created_at = base if m.ordinal == 0 else base + timedelta(minutes=1)
    calls = (
        (
            await db_session.execute(
                select(AssistantToolCall).where(
                    AssistantToolCall.conversation_id == conversation.id
                )
            )
        )
        .scalars()
        .all()
    )
    for c in calls:
        c.created_at = (
            base if c.args.get("sql") == "SELECT count(*) FROM t" else base + timedelta(minutes=1)
        )
    await db_session.commit()

    items = await render_transcript_with_sql(db_session, conversation.id)
    by_text = {item["text"]: item for item in items}
    assert by_text["There are 10."]["sql"] == "SELECT count(*) FROM t"
    assert by_text["The max is 99."]["sql"] == "SELECT max(x) FROM t"
    # User lines never carry SQL.
    assert by_text["how many rows?"]["sql"] is None


async def test_render_transcript_with_sql_ignores_dropped_turns_at_the_cap(
    db_session, conversation, monkeypatch
):
    # A dropped (past-cap) turn's SQL must not leak onto the oldest *visible*
    # turn: with the cap at 2, an older turn that ran SQL falls outside the
    # displayed window, and a visible turn that ran none should stay SQL-free.
    from api.config import settings

    monkeypatch.setattr(settings, "assistant_history_turn_cap", 2)
    await save_turn(
        db_session,
        conversation,
        new_messages_json=_turn_json("old q", "old a"),
        usage=RunUsage(input_tokens=1, output_tokens=1),
        records={"c0": ToolCallRecord(tool="run_sql", args={"sql": "SELECT 1"}, status="ok")},
    )
    await save_turn(
        db_session,
        conversation,
        new_messages_json=_turn_json("mid q", "mid a"),
        usage=RunUsage(input_tokens=1, output_tokens=1),
        records={},
    )
    await save_turn(
        db_session,
        conversation,
        new_messages_json=_turn_json("new q", "new a"),
        usage=RunUsage(input_tokens=1, output_tokens=1),
        records={"c2": ToolCallRecord(tool="run_sql", args={"sql": "SELECT 2"}, status="ok")},
    )
    # Stagger created_at by ordinal so the windows are deterministic under
    # SQLite's whole-second CURRENT_TIMESTAMP (see the note on the test above).
    base = datetime(2026, 1, 1, tzinfo=UTC)
    messages = (
        (
            await db_session.execute(
                select(AssistantMessage).where(AssistantMessage.conversation_id == conversation.id)
            )
        )
        .scalars()
        .all()
    )
    for m in messages:
        m.created_at = base + timedelta(minutes=m.ordinal)
    calls = (
        (
            await db_session.execute(
                select(AssistantToolCall).where(
                    AssistantToolCall.conversation_id == conversation.id
                )
            )
        )
        .scalars()
        .all()
    )
    for c in calls:
        c.created_at = base if c.args.get("sql") == "SELECT 1" else base + timedelta(minutes=2)
    await db_session.commit()

    items = await render_transcript_with_sql(db_session, conversation.id)
    by_text = {item["text"]: item for item in items}
    # The dropped "old" turn isn't rendered at all, and its SQL doesn't bleed
    # onto the oldest visible ("mid") answer.
    assert "old a" not in by_text
    assert by_text["mid a"]["sql"] is None
    assert by_text["new a"]["sql"] == "SELECT 2"


async def test_render_transcript_with_sql_handles_a_turn_with_no_tool_calls(
    db_session, conversation
):
    await save_turn(
        db_session,
        conversation,
        new_messages_json=_turn_json("hi", "hello"),
        usage=RunUsage(input_tokens=1, output_tokens=1),
        records={},
    )
    items = await render_transcript_with_sql(db_session, conversation.id)
    assert [i["sql"] for i in items] == [None, None]


async def test_ordinals_increment_across_turns(db_session, conversation):
    await save_turn(
        db_session,
        conversation,
        new_messages_json=_turn_json("one", "first"),
        usage=RunUsage(input_tokens=1, output_tokens=1),
        records={},
    )
    await save_turn(
        db_session,
        conversation,
        new_messages_json=_turn_json("two", "second"),
        usage=RunUsage(input_tokens=1, output_tokens=1),
        records={},
    )
    history = await load_history(db_session, conversation.id)
    transcript = render_transcript(history)
    assert [i["text"] for i in transcript] == ["one", "first", "two", "second"]
