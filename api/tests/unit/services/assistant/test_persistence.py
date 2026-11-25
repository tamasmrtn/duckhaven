from datetime import UTC, datetime, timedelta

import pytest_asyncio
from conftest import seed_workspace
from pydantic_ai import ModelMessagesTypeAdapter
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.usage import RunUsage
from sqlalchemy import select

from api.models.assistant import AssistantConversation, AssistantMessage, AssistantToolCall
from api.models.user import User
from api.services.assistant.deps import ToolCallRecord
from api.services.assistant.persistence import (
    is_history_truncated,
    load_history,
    render_transcript,
    render_transcript_with_sql,
    save_turn,
)
from api.services.auth import hash_password
from api.services.text_safety import REPLACEMENT


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


async def test_save_turn_persists_tool_call_tables(db_session, conversation):
    await save_turn(
        db_session,
        conversation,
        new_messages_json=_turn_json("query", "done"),
        usage=RunUsage(input_tokens=1, output_tokens=1),
        records={
            "c1": ToolCallRecord(
                tool="run_sql",
                args={"sql": "SELECT * FROM raw.events"},
                status="ok",
                tables=[{"catalog": "acme", "schema_name": "raw", "table": "events"}],
            )
        },
    )
    tool_calls = (await db_session.execute(select(AssistantToolCall))).scalars().all()
    assert len(tool_calls) == 1
    assert tool_calls[0].tables == [{"catalog": "acme", "schema_name": "raw", "table": "events"}]


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


async def test_is_history_truncated_false_under_cap(db_session, conversation):
    await save_turn(
        db_session,
        conversation,
        new_messages_json=_turn_json("q", "a"),
        usage=RunUsage(input_tokens=1, output_tokens=1),
        records={},
    )
    assert await is_history_truncated(db_session, conversation.id) is False


async def test_is_history_truncated_true_over_cap(db_session, conversation, monkeypatch):
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
    assert await is_history_truncated(db_session, conversation.id) is True


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


def test_a_chart_is_a_line_of_its_own_where_its_tool_returned():
    chart = {"type": "chart", "tool_call_id": "c1", "widget": {}, "event": {}, "result_hash": "h"}
    messages = [
        ModelRequest(parts=[UserPromptPart(content="chart it")]),
        ModelResponse(parts=[TextPart(content="Drawing it."), ToolCallPart("show_chart", {})]),
        ModelRequest(
            parts=[
                ToolReturnPart("show_chart", {"shown": True}, "c1", metadata={"chart": chart}),
                # Another tool's result, and one with metadata that is no chart.
                ToolReturnPart("run_sql", {"rows": []}, "c2"),
                ToolReturnPart("run_sql", {"rows": []}, "c3", metadata={"elapsed": 1}),
            ]
        ),
        ModelResponse(parts=[TextPart(content="Revenue rose.")]),
    ]

    items = render_transcript(messages)

    assert [(i["role"], i["text"]) for i in items] == [
        ("user", "chart it"),
        ("assistant", "Drawing it."),
        ("chart", ""),
        ("assistant", "Revenue rose."),
    ]
    assert items[2]["chart"] == chart


def test_a_proposal_is_a_line_of_its_own_where_its_tool_returned():
    card = {"type": "dashboard_proposal", "tool_call_id": "c1", "proposal": {"id": "p-1"}}
    messages = [
        ModelRequest(parts=[UserPromptPart(content="add orders")]),
        ModelResponse(parts=[ToolCallPart("propose_dashboard_changes", {})]),
        ModelRequest(
            parts=[
                ToolReturnPart(
                    "propose_dashboard_changes",
                    {"proposed": True},
                    "c1",
                    metadata={"proposal": card},
                ),
            ]
        ),
        ModelResponse(parts=[TextPart(content="I proposed it.")]),
    ]

    items = render_transcript(messages)

    assert [(i["role"], i["text"]) for i in items] == [
        ("user", "add orders"),
        ("proposal", ""),
        ("assistant", "I proposed it."),
    ]
    assert items[1]["proposal"] == card


def test_an_answer_carries_the_numbers_flagged_on_it():
    messages = [
        ModelRequest(parts=[UserPromptPart(content="how much?")]),
        ModelResponse(
            parts=[TextPart(content="About 4.2M.")],
            metadata={"unsupported_numbers": ["4.2M"]},
        ),
    ]
    # Through the stored JSON, as a conversation is read back.
    stored = ModelMessagesTypeAdapter.validate_json(ModelMessagesTypeAdapter.dump_json(messages))
    assert render_transcript(stored)[1]["unsupported_numbers"] == ["4.2M"]
    plain = render_transcript(messages[:1] + [ModelResponse(parts=[TextPart("Hi")])])
    assert "unsupported_numbers" not in plain[1]


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


async def _stamp(db_session, conversation, base=None):
    """Give every row of this conversation an explicit created_at.

    Required on SQLite, not merely tidy: ``CURRENT_TIMESTAMP`` renders without
    microseconds while SQLAlchemy binds comparison values with them, so a
    server-defaulted timestamp never satisfies the window query's ``>=``. Postgres
    stores real timestamptz and gives every row in a transaction the same
    ``now()``, so this is a test-harness concern only.
    """
    base = base or datetime(2026, 1, 1, tzinfo=UTC)
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
        c.created_at = base
    await db_session.commit()
    return base


async def test_read_pages_are_cited_on_the_turn_that_read_them(db_session, conversation):
    """Same timestamp-window attribution as SQL, and the same trap: without the
    windowing every citation would pile onto one answer."""
    await save_turn(
        db_session,
        conversation,
        new_messages_json=_turn_json("how does time travel work?", "Use the AT clause."),
        usage=RunUsage(input_tokens=1, output_tokens=1),
        records={
            "c1": ToolCallRecord(
                tool="read_doc_page",
                args={"path": "data/snapshots-time-travel.md"},
                status="ok",
            )
        },
    )
    await save_turn(
        db_session,
        conversation,
        new_messages_json=_turn_json("what statements are rejected?", "ATTACH and INSTALL."),
        usage=RunUsage(input_tokens=1, output_tokens=1),
        records={
            "c2": ToolCallRecord(
                tool="read_doc_page", args={"path": "reference/sql-support.md"}, status="ok"
            )
        },
    )
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
        c.created_at = base if "snapshots" in c.args["path"] else base + timedelta(minutes=1)
    await db_session.commit()

    by_text = {i["text"]: i for i in await render_transcript_with_sql(db_session, conversation.id)}

    assert [s["path"] for s in by_text["Use the AT clause."]["sources"]] == [
        "data/snapshots-time-travel.md"
    ]
    assert [s["path"] for s in by_text["ATTACH and INSTALL."]["sources"]] == [
        "reference/sql-support.md"
    ]
    # A user line is never a citation, and neither is a turn that read nothing.
    assert by_text["how does time travel work?"]["sources"] is None


async def test_a_citation_carries_the_page_title_and_a_pinned_url(db_session, conversation):
    """The chip should read the way a reader recognises the page, and link to the
    version this build shipped rather than to whatever the site says today."""
    await save_turn(
        db_session,
        conversation,
        new_messages_json=_turn_json("q", "a"),
        usage=RunUsage(input_tokens=1, output_tokens=1),
        records={
            "c1": ToolCallRecord(
                tool="read_doc_page", args={"path": "reference/sql-support.md"}, status="ok"
            )
        },
    )

    await _stamp(db_session, conversation)

    items = await render_transcript_with_sql(db_session, conversation.id)
    source = next(i for i in items if i["role"] == "assistant")["sources"][0]

    assert source["title"] == "SQL support"
    assert source["url"].endswith("/reference/sql-support/")


async def test_a_page_the_model_failed_to_open_is_not_cited(db_session, conversation):
    """A rejected path is a guess the model then corrected. Citing it shows the
    user a link to a page that does not exist, under a heading promising "each
    page the assistant opened"."""
    await save_turn(
        db_session,
        conversation,
        new_messages_json=_turn_json("is there row-level security?", "No."),
        usage=RunUsage(input_tokens=1, output_tokens=1),
        records={
            "c1": ToolCallRecord(
                tool="read_doc_page",
                args={"path": "concepts/row-level-security.md"},
                status="denied",
            ),
            "c2": ToolCallRecord(
                tool="read_doc_page",
                args={"path": "reference/sql-support.md"},
                status="ok",
            ),
        },
    )

    await _stamp(db_session, conversation)

    items = await render_transcript_with_sql(db_session, conversation.id)
    sources = next(i for i in items if i["role"] == "assistant")["sources"]

    assert [s["path"] for s in sources] == ["reference/sql-support.md"]


async def test_searching_without_reading_cites_nothing(db_session, conversation):
    """A search records its query, not its results. Citing what was searched
    would credit the answer to pages the assistant may never have read."""
    await save_turn(
        db_session,
        conversation,
        new_messages_json=_turn_json("q", "a"),
        usage=RunUsage(input_tokens=1, output_tokens=1),
        records={"c1": ToolCallRecord(tool="search_docs", args={"query": "time travel"})},
    )

    await _stamp(db_session, conversation)

    items = await render_transcript_with_sql(db_session, conversation.id)

    assert next(i for i in items if i["role"] == "assistant")["sources"] is None


async def test_a_page_read_twice_is_cited_once(db_session, conversation):
    await save_turn(
        db_session,
        conversation,
        new_messages_json=_turn_json("q", "a"),
        usage=RunUsage(input_tokens=1, output_tokens=1),
        records={
            "c1": ToolCallRecord(
                tool="read_doc_page", args={"path": "reference/sql-support.md"}, status="ok"
            ),
            "c2": ToolCallRecord(
                tool="read_doc_page", args={"path": "reference/sql-support.md"}, status="ok"
            ),
        },
    )

    await _stamp(db_session, conversation)

    items = await render_transcript_with_sql(db_session, conversation.id)

    assert len(next(i for i in items if i["role"] == "assistant")["sources"]) == 1


async def test_a_turn_holding_text_the_database_cannot_keep_is_still_saved(
    db_session, conversation
):
    """One NUL in a query result used to fail the save, losing the answer, the question
    and the audit rows with it."""
    messages = [
        ModelRequest(parts=[UserPromptPart(content="what is in the cell?")]),
        ModelResponse(parts=[TextPart(content="It holds a\x00b.")]),
    ]
    await save_turn(
        db_session,
        conversation,
        new_messages_json=ModelMessagesTypeAdapter.dump_json(messages),
        usage=RunUsage(input_tokens=1, output_tokens=1),
        records={
            "c1": ToolCallRecord(
                tool="run_sql",
                args={"sql": "SELECT '\x00'", "n\x00ote": ["x\x00"]},
                status="error",
                detail="Query failed: bad byte \x00",
                tables=[{"catalog": "c", "schema_name": "s", "table": "t\x00"}],
            )
        },
    )

    saved = (await db_session.execute(select(AssistantMessage))).scalar_one()
    call = (await db_session.execute(select(AssistantToolCall))).scalar_one()
    assert saved.payload[-1]["parts"][0]["content"] == f"It holds a{REPLACEMENT}b."
    assert call.args == {
        "sql": f"SELECT '{REPLACEMENT}'",
        f"n{REPLACEMENT}ote": [f"x{REPLACEMENT}"],
    }
    assert call.detail == f"Query failed: bad byte {REPLACEMENT}"
    assert call.tables == [{"catalog": "c", "schema_name": "s", "table": f"t{REPLACEMENT}"}]
    assert "\x00" not in repr(saved.payload)
