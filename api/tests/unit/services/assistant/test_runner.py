"""End-to-end turn tests: scripted model + real governed loopback (SQLite).

The model is faked with FunctionModel; the tools make real loopback HTTP calls into
``api_app`` (via the ``client`` fixture's dependency overrides), so these exercise
identity minting, the gateway, the governance audit hooks, and persistence together.
"""

import pytest
import pytest_asyncio
from conftest import seed_workspace
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from api.config import settings
from api.models.assistant import AssistantConversation, AssistantMessage, AssistantToolCall
from api.models.user import User
from api.models.workspace import WorkspaceMember
from api.services.assistant.agent import get_agent
from api.services.assistant.runner import run_turn, stream_turn

from .conftest import parse_sse, scripted_model, text_step, tool_step


@pytest.fixture(autouse=True)
def _enable_assistant(monkeypatch):
    monkeypatch.setattr(settings, "assistant_enabled", True)
    monkeypatch.setattr(settings, "assistant_service_account_slug", "assistant")


@pytest_asyncio.fixture
async def factory(db_engine):
    return async_sessionmaker(db_engine, expire_on_commit=False)


async def _seed(db_session, *, sa_role: str):
    """Create a human owner, a workspace, and the assistant service account."""
    human = User(email="human@p.local", name="Human", role="user")
    sa = User(
        email="assistant@service-account.local",
        name="Assistant",
        role="user",
        auth_provider="service_account",
    )
    db_session.add_all([human, sa])
    await db_session.commit()
    ws, catalog = await seed_workspace(db_session, user_id=human.id, role="owner")
    db_session.add(WorkspaceMember(workspace_id=ws.id, user_id=sa.id, role=sa_role))
    conv = AssistantConversation(workspace_id=ws.id, user_id=human.id, title="t")
    db_session.add(conv)
    await db_session.commit()
    await db_session.refresh(conv)
    return ws, catalog, conv


async def test_run_turn_browses_and_persists(client, db_session, factory):
    ws, _catalog, conv = await _seed(db_session, sa_role="reader")
    responses = [
        tool_step("list_catalogs", {}),
        text_step("I found the catalogs."),
    ]
    with get_agent().override(model=scripted_model(responses)):
        answer = await run_turn(
            factory,
            conversation_id=conv.id,
            workspace_id=ws.id,
            workspace_slug=ws.slug,
            prompt="what catalogs exist?",
        )
    assert answer == "I found the catalogs."

    async with factory() as db:
        messages = (await db.execute(select(AssistantMessage))).scalars().all()
        assert len(messages) == 1
        tool_calls = (await db.execute(select(AssistantToolCall))).scalars().all()
        assert [tc.tool for tc in tool_calls] == ["list_catalogs"]
        assert tool_calls[0].status == "ok"


async def test_read_only_assistant_refuses_write(client, db_session, factory):
    ws, _catalog, conv = await _seed(db_session, sa_role="reader")
    # The model tries a write, then (after the ModelRetry refusal) answers.
    responses = [
        tool_step("run_sql", {"sql": "DELETE FROM t"}),
        text_step("I cannot do that; it is a write."),
    ]
    with get_agent().override(model=scripted_model(responses)):
        answer = await run_turn(
            factory,
            conversation_id=conv.id,
            workspace_id=ws.id,
            workspace_slug=ws.slug,
            prompt="delete everything",
        )
    assert "cannot" in answer.lower()
    async with factory() as db:
        tool_calls = (await db.execute(select(AssistantToolCall))).scalars().all()
        assert tool_calls[0].tool == "run_sql"
        assert tool_calls[0].status == "denied"  # ModelRetry refusal recorded


async def test_stream_turn_emits_tokens_and_done(client, db_session, factory):
    ws, _catalog, conv = await _seed(db_session, sa_role="reader")
    responses = [text_step("Hello there.")]
    with get_agent().override(model=scripted_model(responses)):
        chunks = [
            frame
            async for frame in stream_turn(
                factory,
                conversation_id=conv.id,
                workspace_id=ws.id,
                workspace_slug=ws.slug,
                prompt="hi",
                catalog=None,
            )
        ]
    frames = parse_sse(chunks)
    kinds = [f["type"] for f in frames]
    assert "done" in kinds
    assert any(f.get("text") for f in frames if f["type"] == "token")


async def test_write_with_grant_requests_approval(client, db_session, factory):
    # Writer service account → writes are offered, but must be approved.
    ws, _catalog, conv = await _seed(db_session, sa_role="writer")
    responses = [tool_step("run_sql", {"sql": "DELETE FROM t"})]
    with get_agent().override(model=scripted_model(responses)):
        chunks = [
            frame
            async for frame in stream_turn(
                factory,
                conversation_id=conv.id,
                workspace_id=ws.id,
                workspace_slug=ws.slug,
                prompt="delete everything",
                catalog=None,
            )
        ]
    frames = parse_sse(chunks)
    approval = [f for f in frames if f["type"] == "approval_required"]
    assert len(approval) == 1
    assert approval[0]["sql"] == "DELETE FROM t"
    assert approval[0]["tool_call_id"]


async def test_first_turn_generates_a_title(client, db_session, factory):
    from api.services.assistant.title import get_title_agent

    ws, _catalog, conv = await _seed(db_session, sa_role="reader")
    conv.title = "New conversation"
    await db_session.commit()

    with (
        get_agent().override(model=scripted_model([text_step("Hi there.")])),
        get_title_agent().override(model=scripted_model([text_step("Greeting the assistant")])),
    ):
        async for _ in stream_turn(
            factory,
            conversation_id=conv.id,
            workspace_id=ws.id,
            workspace_slug=ws.slug,
            prompt="hello",
            catalog=None,
        ):
            pass

    async with factory() as db:
        updated = await db.get(AssistantConversation, conv.id)
        assert updated.title == "Greeting the assistant"


async def test_propose_sql_edit_emits_propose_edit_frame(client, db_session, factory):
    ws, _catalog, conv = await _seed(db_session, sa_role="reader")
    responses = [
        tool_step(
            "propose_sql_edit",
            {"sql": "SELECT 1", "explanation": "a minimal query"},
        ),
        text_step("I proposed a query in your editor."),
    ]
    with get_agent().override(model=scripted_model(responses)):
        chunks = [
            frame
            async for frame in stream_turn(
                factory,
                conversation_id=conv.id,
                workspace_id=ws.id,
                workspace_slug=ws.slug,
                prompt="write me a query",
                catalog=None,
            )
        ]
    frames = parse_sse(chunks)
    edits = [f for f in frames if f["type"] == "propose_edit"]
    assert len(edits) == 1
    assert edits[0]["sql"] == "SELECT 1"
    assert edits[0]["explanation"] == "a minimal query"
    # It is surfaced as its own frame, not a generic tool_call line.
    assert not any(f["type"] == "tool_call" and f["tool"] == "propose_sql_edit" for f in frames)
