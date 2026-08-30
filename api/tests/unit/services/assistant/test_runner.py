"""End-to-end turn tests: scripted model + real governed loopback (SQLite).

The model is faked with FunctionModel; the tools make real loopback HTTP calls into
``api_app`` (via the ``client`` fixture's dependency overrides), so these exercise
identity minting, the gateway, the governance audit hooks, and persistence together.
"""

import asyncio
import contextlib

import pytest
import pytest_asyncio
from conftest import seed_workspace
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from api.config import settings
from api.models.assistant import AssistantConversation, AssistantMessage, AssistantToolCall
from api.models.user import User
from api.models.workspace import WorkspaceMember
from api.services.assistant.agent import get_agent
from api.services.assistant.persistence import render_transcript_with_sql
from api.services.assistant.runner import stream_turn

from .conftest import hanging_model, parse_sse, scripted_model, text_step, tool_step


@pytest.fixture(autouse=True)
def _enable_assistant(monkeypatch):
    monkeypatch.setattr(settings, "assistant_enabled", True)


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


async def _run_stream(factory, ws, conv, prompt):
    async for _ in stream_turn(
        factory,
        conversation_id=conv.id,
        workspace_id=ws.id,
        workspace_slug=ws.slug,
        prompt=prompt,
        catalog=None,
    ):
        pass


async def test_turn_browses_and_persists_and_stamps_principal(client, db_session, factory):
    ws, _catalog, conv = await _seed(db_session, sa_role="reader")
    responses = [
        tool_step("list_catalogs", {}),
        text_step("I found the catalogs."),
    ]
    with get_agent().override(model=scripted_model(responses)):
        await _run_stream(factory, ws, conv, "what catalogs exist?")

    async with factory() as db:
        messages = (await db.execute(select(AssistantMessage))).scalars().all()
        assert len(messages) == 1
        tool_calls = (await db.execute(select(AssistantToolCall))).scalars().all()
        assert [tc.tool for tc in tool_calls] == ["list_catalogs"]
        assert tool_calls[0].status == "ok"
        # The conversation is attributed to the acting service account.
        updated = await db.get(AssistantConversation, conv.id)
        sa = (
            await db.execute(select(User).where(User.email == "assistant@service-account.local"))
        ).scalar_one()
        assert updated.service_account_id == sa.id


def _talk_call_talk(first: str, second: str) -> FunctionModel:
    """A model that says something, calls a tool, then says something else.

    Two `ModelResponse`s, so two text segments — the shape that exposes both how
    a text part starts and where one displayed message ends and the next begins.
    """

    def function(messages, info) -> ModelResponse:  # non-streaming fallback
        return ModelResponse(parts=[TextPart(second)])

    requests = iter([first, second])

    async def stream_function(messages, info):
        text = next(requests)
        for word in text.split(" "):
            yield word + " "
        if text is first:
            # Same response: text *and* a tool call, so the run continues and a
            # second text part is started later.
            yield {1: DeltaToolCall(name="list_catalogs", json_args="{}")}

    return FunctionModel(function, stream_function=stream_function)


async def _collect_stream(factory, ws, conv, prompt) -> list[dict]:
    chunks: list[str] = []
    async for chunk in stream_turn(
        factory,
        conversation_id=conv.id,
        workspace_id=ws.id,
        workspace_slug=ws.slug,
        prompt=prompt,
        catalog=None,
    ):
        chunks.append(chunk)
    return parse_sse(chunks)


async def test_stream_emits_every_word_of_every_text_segment(client, db_session, factory):
    """A turn's streamed text must carry every word that gets persisted.

    The parts manager puts a text part's *first* chunk on the PartStartEvent and
    only the rest on TextPartDeltas, so mapping deltas alone drops the opening
    words of every text segment.
    """
    ws, _catalog, conv = await _seed(db_session, sa_role="reader")
    first = "Let me find the customer table first."
    second = "Catalog listing is denied for my service account."
    with get_agent().override(model=_talk_call_talk(first, second)):
        frames = await _collect_stream(factory, ws, conv, "find the customer table")

    streamed = "".join(f["text"] for f in frames if f["type"] == "token")
    # Both segments arrive whole — no segment loses its opening words.
    assert streamed.split() == (first + " " + second).split()


async def test_streamed_segments_match_the_persisted_transcript(client, db_session, factory):
    """What streams must be grouped into the same messages the transcript shows.

    Token frames carry no message identity of their own, so without the `start`
    marker a multi-step reply streams as one run-on bubble and then re-shuffles
    into several messages the moment the turn settles. Pinning the two groupings
    to each other is what stops them drifting apart again.
    """
    ws, _catalog, conv = await _seed(db_session, sa_role="reader")
    first = "Let me find the customer table first."
    second = "Catalog listing is denied for my service account."
    with get_agent().override(model=_talk_call_talk(first, second)):
        frames = await _collect_stream(factory, ws, conv, "find the customer table")

    # Split the streamed tokens the way the client does.
    segments: list[str] = []
    for frame in frames:
        if frame["type"] != "token":
            continue
        if frame.get("start") or not segments:
            segments.append(frame["text"])
        else:
            segments[-1] += frame["text"]

    async with factory() as db:
        transcript = await render_transcript_with_sql(db, conv.id)
    persisted = [item["text"] for item in transcript if item["role"] == "assistant"]

    assert len(persisted) == 2  # the turn really did produce two messages
    assert segments == persisted


async def test_read_only_assistant_refuses_write(client, db_session, factory):
    ws, _catalog, conv = await _seed(db_session, sa_role="reader")
    # The model tries a write, then (after the ModelRetry refusal) answers.
    responses = [
        tool_step("run_sql", {"sql": "DELETE FROM t"}),
        text_step("I cannot do that; it is a write."),
    ]
    with get_agent().override(model=scripted_model(responses)):
        await _run_stream(factory, ws, conv, "delete everything")
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


async def test_stop_cancels_and_discards_the_turn(client, db_session, factory):
    # Real Stop: closing the stream mid-turn (a client disconnect / the Stop
    # button) cancels the run and persists nothing.
    ws, _catalog, conv = await _seed(db_session, sa_role="reader")
    gate = asyncio.Event()  # never set → the model blocks mid-stream
    with get_agent().override(model=hanging_model(gate)):
        gen = stream_turn(
            factory,
            conversation_id=conv.id,
            workspace_id=ws.id,
            workspace_slug=ws.slug,
            prompt="hi",
            catalog=None,
        )
        # Start pulling the stream so the turn actually begins (mints identity,
        # starts the model run). The model emits one token before it blocks, so
        # take that frame first; the *next* pull is the one that can't complete.
        first = await asyncio.wait_for(gen.__anext__(), timeout=5)
        assert '"type": "token"' in first
        # Then cancel a pull mid-turn — exactly what Starlette does to the body
        # generator on client disconnect. Bounded so a cancellation that fails to
        # propagate fails the test instead of hanging.
        pull = asyncio.ensure_future(gen.__anext__())
        await asyncio.sleep(0.3)
        assert not pull.done()  # the run is under way and blocked in the model
        pull.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.wait_for(pull, timeout=5)
        # Cancelling the pull runs the generator's finally, which cancels the run.
        with contextlib.suppress(RuntimeError, StopAsyncIteration):
            await asyncio.wait_for(gen.aclose(), timeout=5)

    async with factory() as db:
        messages = (
            (
                await db.execute(
                    select(AssistantMessage).where(AssistantMessage.conversation_id == conv.id)
                )
            )
            .scalars()
            .all()
        )
        assert messages == []  # the cancelled turn left no message row
        tool_calls = (
            (
                await db.execute(
                    select(AssistantToolCall).where(AssistantToolCall.conversation_id == conv.id)
                )
            )
            .scalars()
            .all()
        )
        assert tool_calls == []


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
    # No selection was sent with this turn, so the edit is not scoped.
    assert edits[0]["scoped"] is False
    # It is surfaced as its own frame, not a generic tool_call line.
    assert not any(f["type"] == "tool_call" and f["tool"] == "propose_sql_edit" for f in frames)


async def test_propose_sql_edit_with_selection_is_scoped(client, db_session, factory):
    ws, _catalog, conv = await _seed(db_session, sa_role="reader")
    responses = [
        tool_step(
            "propose_sql_edit",
            {"sql": "id = 2", "explanation": "changed the filter"},
        ),
        text_step("I updated the selected fragment."),
    ]
    with get_agent().override(model=scripted_model(responses)):
        chunks = [
            frame
            async for frame in stream_turn(
                factory,
                conversation_id=conv.id,
                workspace_id=ws.id,
                workspace_slug=ws.slug,
                prompt="change this to id = 2",
                catalog=None,
                selection_sql="id = 1",
            )
        ]
    frames = parse_sse(chunks)
    edits = [f for f in frames if f["type"] == "propose_edit"]
    assert len(edits) == 1
    assert edits[0]["scoped"] is True


async def test_request_limit_surfaces_friendly_error(client, db_session, factory, monkeypatch):
    monkeypatch.setattr(settings, "assistant_request_limit", 1)
    ws, _catalog, conv = await _seed(db_session, sa_role="reader")
    # A tool call needs a second model request to process its result, exceeding 1.
    responses = [tool_step("list_catalogs", {}), text_step("never reached")]
    with get_agent().override(model=scripted_model(responses)):
        chunks = [
            frame
            async for frame in stream_turn(
                factory,
                conversation_id=conv.id,
                workspace_id=ws.id,
                workspace_slug=ws.slug,
                prompt="loop forever",
                catalog=None,
            )
        ]
    frames = parse_sse(chunks)
    errors = [f for f in frames if f["type"] == "error"]
    assert errors and "step limit" in errors[0]["message"]


def test_safe_error_message_whitelists_known_types():
    from api.services.assistant.gateway import GatewayError
    from api.services.assistant.identity import AssistantIdentityError
    from api.services.assistant.runner import _safe_error_message

    assert _safe_error_message(GatewayError("Access denied: x")) == "Access denied: x"
    assert "not configured" in _safe_error_message(AssistantIdentityError("not configured"))
    # An unexpected error is not leaked verbatim.
    assert _safe_error_message(ValueError("secret internal db url")) == (
        "The assistant hit an internal error."
    )
