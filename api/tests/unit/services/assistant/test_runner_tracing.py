"""Assistant turns emit an ``assistant.turn`` span; the content toggle is honored.

Reuses the scripted-model + real-loopback harness from ``test_runner``. In unit
tests no OTel SDK is configured except the session ``span_exporter`` fixture, whose
in-memory provider the runner's proxy tracer resolves to at span-creation time.
"""

import pytest
import pytest_asyncio
from conftest import seed_workspace
from opentelemetry import trace
from pydantic_ai.messages import ModelResponse
from pydantic_ai.models.function import FunctionModel
from sqlalchemy.ext.asyncio import async_sessionmaker

from api.config import settings
from api.models.assistant import AssistantConversation
from api.models.user import User
from api.models.workspace import WorkspaceMember
from api.services.assistant.agent import _instrumentation, get_agent
from api.services.assistant.runner import stream_turn

from .conftest import scripted_model, text_step


@pytest.fixture(autouse=True)
def _enable_assistant(monkeypatch):
    monkeypatch.setattr(settings, "assistant_enabled", True)


@pytest_asyncio.fixture
async def factory(db_engine):
    return async_sessionmaker(db_engine, expire_on_commit=False)


async def _seed(db_session):
    """Create a human owner, a workspace, the assistant service account, and a convo."""
    human = User(email="human@p.local", name="Human", role="user")
    sa = User(
        email="assistant@service-account.local",
        name="Assistant",
        role="user",
        auth_provider="service_account",
    )
    db_session.add_all([human, sa])
    await db_session.commit()
    ws, _catalog = await seed_workspace(db_session, user_id=human.id, role="owner")
    db_session.add(WorkspaceMember(workspace_id=ws.id, user_id=sa.id, role="reader"))
    conv = AssistantConversation(workspace_id=ws.id, user_id=human.id, title="t")
    db_session.add(conv)
    await db_session.commit()
    await db_session.refresh(conv)
    return ws, conv


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


def _failing_model(exc: Exception) -> FunctionModel:
    """A model that raises on every request, to fail a turn inside the agent run."""

    def function(messages, info) -> ModelResponse:
        raise exc

    async def stream_function(messages, info):
        raise exc
        yield  # pragma: no cover — makes this an async generator

    return FunctionModel(function, stream_function=stream_function)


async def test_turn_emits_assistant_turn_span(client, db_session, factory, span_exporter):
    ws, conv = await _seed(db_session)
    with get_agent().override(model=scripted_model([text_step("Hello there.")])):
        await _run_stream(factory, ws, conv, "hi")

    spans = [s for s in span_exporter.get_finished_spans() if s.name == "assistant.turn"]
    assert len(spans) == 1
    span = spans[0]
    assert span.attributes["duckhaven.conversation_id"] == str(conv.id)
    assert span.attributes["duckhaven.workspace_id"] == str(ws.id)
    assert span.attributes["duckhaven.assistant.resumed"] is False
    assert span.status.status_code != trace.StatusCode.ERROR


async def test_failed_turn_sets_span_error_status(client, db_session, factory, span_exporter):
    ws, conv = await _seed(db_session)
    with get_agent().override(model=_failing_model(RuntimeError("intentional-error"))):
        await _run_stream(factory, ws, conv, "hi")

    spans = [s for s in span_exporter.get_finished_spans() if s.name == "assistant.turn"]
    assert len(spans) == 1
    assert spans[0].status.status_code == trace.StatusCode.ERROR
    assert len(spans[0].events) >= 1  # record_exception


def test_instrumentation_reflects_content_toggle(monkeypatch):
    monkeypatch.setattr(settings, "assistant_trace_include_content", False)
    assert _instrumentation().settings.include_content is False
    monkeypatch.setattr(settings, "assistant_trace_include_content", True)
    assert _instrumentation().settings.include_content is True
