"""What the feedback-mining script finds, and what it deliberately does not.

Loaded by path like the migration tests, because it ships in ``scripts/`` rather
than the API package — an operator runs it, the application never imports it.

The interesting property is restraint. Three of the four signals are exact
queries over rows the assistant already writes; the fourth is a heuristic that
will over-report, and the tests pin both its usefulness and its limits so nobody
later mistakes it for a measurement.
"""

import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import pytest_asyncio
from pydantic_ai.usage import RunUsage
from sqlalchemy import select

from api.models.assistant import AssistantConversation, AssistantMessage, AssistantToolCall
from api.models.user import User
from api.models.workspace import Workspace
from api.services.assistant.deps import ToolCallRecord
from api.services.assistant.persistence import save_turn
from api.services.auth import hash_password

SCRIPT = Path(__file__).resolve().parents[5] / "scripts" / "assistant-mine-feedback.py"


def _load():
    spec = importlib.util.spec_from_file_location("mine_feedback", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


miner = _load()

SINCE = datetime(2020, 1, 1, tzinfo=UTC)


def _turn(question: str, answer: str | None) -> bytes:
    import json

    messages = [{"kind": "request", "parts": [{"part_kind": "user-prompt", "content": question}]}]
    if answer is not None:
        messages.append({"kind": "response", "parts": [{"part_kind": "text", "content": answer}]})
    else:
        # A turn that ran out of steps: tool calls, then nothing to say.
        messages.append(
            {
                "kind": "response",
                "parts": [{"part_kind": "tool-call", "tool_name": "run_sql", "args": {}}],
            }
        )
    return json.dumps(messages).encode()


@pytest_asyncio.fixture
async def conversation(db_session) -> AssistantConversation:
    user = User(email="u@p.local", password_hash=hash_password("pw"), name="U", role="user")
    db_session.add(user)
    await db_session.commit()
    workspace = Workspace(slug="w", name="W")
    db_session.add(workspace)
    await db_session.commit()
    conv = AssistantConversation(
        workspace_id=workspace.id, user_id=user.id, title="Exploring DuckHaven"
    )
    db_session.add(conv)
    await db_session.commit()
    await db_session.refresh(conv)
    return conv


async def _stamp(db_session, conversation, when: datetime):
    """Explicit timestamps: SQLite's CURRENT_TIMESTAMP renders without
    microseconds and will not compare against a bound datetime reliably."""
    for model in (AssistantMessage, AssistantToolCall):
        rows = (
            (
                await db_session.execute(
                    select(model).where(model.conversation_id == conversation.id)
                )
            )
            .scalars()
            .all()
        )
        for row in rows:
            row.created_at = when
    await db_session.commit()


# ── Signal 1: the documentation did not answer it ─────────────────────────────


async def test_a_search_that_found_nothing_is_the_headline_signal(db_session, conversation):
    """The strongest of the four: somebody asked something the documentation does
    not cover, and the assistant recorded that it knew."""
    await save_turn(
        db_session,
        conversation,
        new_messages_json=_turn("does DuckHaven do row-level security?", "Not that I can find."),
        usage=RunUsage(input_tokens=1, output_tokens=1),
        records={
            "c1": ToolCallRecord(
                tool="search_docs",
                args={"query": "row level security"},
                status="ok",
                detail="no_results",
            )
        },
    )
    await _stamp(db_session, conversation, datetime(2026, 6, 1, tzinfo=UTC))

    findings = await miner.mine(db_session, SINCE)

    assert [f["query"] for f in findings["unanswered_by_docs"]] == ["row level security"]


async def test_a_search_that_found_something_is_not_a_finding(db_session, conversation):
    await save_turn(
        db_session,
        conversation,
        new_messages_json=_turn("how does time travel work?", "Use the AT clause."),
        usage=RunUsage(input_tokens=1, output_tokens=1),
        records={
            "c1": ToolCallRecord(tool="search_docs", args={"query": "time travel"}, status="ok")
        },
    )
    await _stamp(db_session, conversation, datetime(2026, 6, 1, tzinfo=UTC))

    findings = await miner.mine(db_session, SINCE)

    assert findings["unanswered_by_docs"] == []


# ── Signal 2: a documentation tool failed ─────────────────────────────────────


async def test_a_failed_documentation_read_is_reported(db_session, conversation):
    await save_turn(
        db_session,
        conversation,
        new_messages_json=_turn("what is in the gone page?", "I could not open it."),
        usage=RunUsage(input_tokens=1, output_tokens=1),
        records={
            "c1": ToolCallRecord(
                tool="read_doc_page",
                args={"path": "reference/gone.md"},
                status="denied",
                detail="No documentation page at 'reference/gone.md'.",
            )
        },
    )
    await _stamp(db_session, conversation, datetime(2026, 6, 1, tzinfo=UTC))

    findings = await miner.mine(db_session, SINCE)

    assert findings["docs_tool_failed"][0]["path"] == "reference/gone.md"


# ── Signal 3: the user got nothing ────────────────────────────────────────────


async def test_a_turn_that_produced_no_answer_is_reported(db_session, conversation):
    """Ran out of its request limit, or failed. Either way the user got nothing,
    and that is worth a person's attention whatever the cause."""
    await save_turn(
        db_session,
        conversation,
        new_messages_json=_turn("can I use time travel on a view?", None),
        usage=RunUsage(input_tokens=1, output_tokens=1),
        records={},
    )
    await _stamp(db_session, conversation, datetime(2026, 6, 1, tzinfo=UTC))

    findings = await miner.mine(db_session, SINCE)

    assert findings["turn_produced_no_answer"][0]["question"] == (
        "can I use time travel on a view?"
    )
    # Counted once. A turn with no answer is not also an uncited product answer.
    assert findings["answered_without_docs"] == []


# ── Signal 4: the heuristic one ───────────────────────────────────────────────


async def test_a_product_question_answered_without_opening_docs_is_flagged(
    db_session, conversation
):
    """The only signal that can catch a confident wrong answer, which leaves no
    trace anywhere else."""
    await save_turn(
        db_session,
        conversation,
        new_messages_json=_turn(
            "does DuckHaven support snapshot retention?", "Yes, set it in table settings."
        ),
        usage=RunUsage(input_tokens=1, output_tokens=1),
        records={},
    )
    await _stamp(db_session, conversation, datetime(2026, 6, 1, tzinfo=UTC))

    findings = await miner.mine(db_session, SINCE)

    assert len(findings["answered_without_docs"]) == 1


async def test_a_product_question_that_did_open_docs_is_not_flagged(db_session, conversation):
    await save_turn(
        db_session,
        conversation,
        new_messages_json=_turn(
            "does DuckHaven support snapshot retention?", "No — it does not expire snapshots."
        ),
        usage=RunUsage(input_tokens=1, output_tokens=1),
        records={
            "c1": ToolCallRecord(
                tool="read_doc_page",
                args={"path": "guides/snapshots-time-travel.md"},
                status="ok",
            )
        },
    )
    await _stamp(db_session, conversation, datetime(2026, 6, 1, tzinfo=UTC))

    findings = await miner.mine(db_session, SINCE)

    assert findings["answered_without_docs"] == []


async def test_one_cited_turn_does_not_vouch_for_its_neighbours(db_session, conversation):
    """A regression. The first version asked "did this *conversation* ever open a
    page?", so a single cited turn silenced the signal for every other turn beside
    it — and an uncited answer sitting next to a cited one is exactly the case
    worth finding. Only an end-to-end run against seeded data exposed it; every
    unit test until this one used a single turn per conversation.
    """
    await save_turn(
        db_session,
        conversation,
        new_messages_json=_turn("how does time travel work?", "Use the AT clause."),
        usage=RunUsage(input_tokens=1, output_tokens=1),
        records={
            "c1": ToolCallRecord(
                tool="read_doc_page",
                args={"path": "guides/snapshots-time-travel.md"},
                status="ok",
            )
        },
    )
    await save_turn(
        db_session,
        conversation,
        new_messages_json=_turn(
            "does DuckHaven support snapshot retention?", "Yes, in table settings."
        ),
        usage=RunUsage(input_tokens=1, output_tokens=1),
        records={},
    )
    base = datetime(2026, 6, 1, tzinfo=UTC)
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

    findings = await miner.mine(db_session, SINCE)

    assert [f["question"] for f in findings["answered_without_docs"]] == [
        "does DuckHaven support snapshot retention?"
    ]


async def test_a_data_question_is_not_mistaken_for_a_product_question(db_session, conversation):
    await save_turn(
        db_session,
        conversation,
        new_messages_json=_turn("what was revenue last month?", "1.2M."),
        usage=RunUsage(input_tokens=1, output_tokens=1),
        records={},
    )
    await _stamp(db_session, conversation, datetime(2026, 6, 1, tzinfo=UTC))

    findings = await miner.mine(db_session, SINCE)

    assert findings["answered_without_docs"] == []


@pytest.mark.parametrize(
    ("question", "matches"),
    [
        ("How do I configure an agent?", True),
        ("Does DuckHaven support LDAP?", True),
        ("what is time travel", True),
        ("show me the top 10 customers by spend", False),
        ("join orders to customers", False),
    ],
)
def test_the_product_vocabulary_is_generous_but_not_indiscriminate(question, matches):
    """It will over-report, deliberately: a candidate a human discards costs a
    second, one never surfaced costs a blind spot. But it must not treat ordinary
    data questions as product questions, or the report becomes noise."""
    assert bool(miner._PRODUCT_VOCABULARY.search(question)) is matches


# ── Only the two signals that carry a question become cases ───────────────────


def test_only_question_bearing_signals_become_candidate_cases():
    findings = {
        "unanswered_by_docs": [{"query": "row level security"}],
        "docs_tool_failed": [{"path": "reference/gone.md"}],
        "turn_produced_no_answer": [{"question": "anything"}],
        "answered_without_docs": [{"question": "does DuckHaven do X?"}],
    }

    cases = miner.as_candidate_cases(findings)

    assert [c["inputs"]["question"] for c in cases] == [
        "row level security",
        "does DuckHaven do X?",
    ]


def test_every_candidate_case_tells_the_reviewer_what_to_confirm():
    """A mined case is a lead, not a finding. Promoting one without checking
    would put a guess into the golden set, where it becomes a standard."""
    findings = {
        "unanswered_by_docs": [{"query": "row level security"}],
        "docs_tool_failed": [],
        "turn_produced_no_answer": [],
        "answered_without_docs": [{"question": "does DuckHaven do X?"}],
    }

    cases = miner.as_candidate_cases(findings)

    assert all("Mined from production" in c["metadata"]["note"] for c in cases)
    assert all(
        "Confirm" in c["metadata"]["note"] or "Check" in c["metadata"]["note"] for c in cases
    )


def test_nothing_found_produces_no_cases():
    empty = {
        k: []
        for k in (
            "unanswered_by_docs",
            "docs_tool_failed",
            "turn_produced_no_answer",
            "answered_without_docs",
        )
    }

    assert miner.as_candidate_cases(empty) == []
    assert "Documentation search found nothing: 0" in miner.report(empty, SINCE)
