"""Submitting a run for another caller: a dashboard's widgets go through the same
path as someone's own run, and keep ``origin="dashboard"`` on every row it writes."""

import json
import uuid

import pytest
import pytest_asyncio
from conftest import seed_workspace
from fastapi import HTTPException
from prometheus_client import REGISTRY

from api.config import settings
from api.models.agent import Agent
from api.models.query import Query
from api.models.user import User
from api.schemas.query import QueryCreate
from api.services import query_submit
from api.services.agent_registry import registry
from api.services.compute.backends import get_backend
from duckhaven_shared.protocol import FrameType


class MockWebSocket:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_text(self, text: str) -> None:
        self.sent.append(json.loads(text))


@pytest_asyncio.fixture
async def user(db_session):
    u = User(email="s@submit.local", password_hash="x", name="Submitter", role="user")
    db_session.add(u)
    await db_session.commit()
    return u


@pytest_asyncio.fixture
async def workspace(db_session, user):
    ws, _catalog = await seed_workspace(db_session, user_id=user.id)
    return ws


@pytest_asyncio.fixture
async def agent(db_session):
    a = Agent(
        name="submit-agent",
        status="healthy",
        capabilities={"duckdb_version": "1.5.5", "extensions": ["httpfs"]},
    )
    db_session.add(a)
    await db_session.commit()
    return a


@pytest.fixture
def connected(agent):
    ws = MockWebSocket()
    registry.register(agent.id, ws)  # type: ignore[arg-type]
    yield agent, ws
    registry.unregister(agent.id)


@pytest.fixture
def elastic_enabled(monkeypatch):
    monkeypatch.setattr(settings, "elastic_compute_enabled", True)
    monkeypatch.setattr(settings, "elastic_provider", "null")
    monkeypatch.setattr(settings, "elastic_max_agents_per_pool", 1)
    backend = get_backend("null")
    backend._instances.clear()
    yield
    backend._instances.clear()


def _submitted() -> float:
    labels = {"replica_id": settings.replica_id}
    return REGISTRY.get_sample_value("duckhaven_queries_submitted_total", labels) or 0


async def test_a_dashboard_run_is_dispatched_with_its_origin_tags_and_client(
    db_session, fake_polaris, workspace, user, connected
):
    agent, ws = connected
    before = _submitted()
    body = QueryCreate(
        sql="SELECT 1", agent_id=agent.id, tags={"dashboard": "sales", "widget": "revenue"}
    )

    query = await query_submit.submit_query(
        db_session,
        workspace,
        body,
        user,
        fake_polaris,
        origin="dashboard",
        client=("duckhaven-web", "1.2"),
    )

    assert (query.status, query.agent_id, query.origin) == ("queued", agent.id, "dashboard")
    assert query.tags == {"dashboard": "sales", "widget": "revenue"}
    assert (query.client_name, query.client_version) == ("duckhaven-web", "1.2")
    [frame] = [f for f in ws.sent if f["type"] == FrameType.DISPATCH_QUERY]
    assert frame["payload"]["query_id"] == str(query.id)
    # Counted with the runs people wait on.
    assert _submitted() == before + 1


async def test_a_dashboard_run_with_no_compute_parks_under_its_own_origin(
    db_session, fake_polaris, workspace, user, elastic_enabled
):
    """An interactive pool run parks as `elastic`; a widget's keeps `dashboard`."""
    query = await query_submit.submit_query(
        db_session, workspace, QueryCreate(sql="SELECT 1"), user, fake_polaris, origin="dashboard"
    )

    assert (query.status, query.agent_id, query.origin) == ("queued", None, "dashboard")


async def test_a_refused_dashboard_run_is_recorded_under_its_own_origin(
    db_session, fake_polaris, workspace, user, agent
):
    """The failed row History shows for an unreachable agent keeps the caller's origin."""
    body = QueryCreate(sql="SELECT 1", agent_id=agent.id)

    with pytest.raises(HTTPException) as refused:
        await query_submit.submit_query(
            db_session, workspace, body, user, fake_polaris, origin="dashboard"
        )

    assert refused.value.status_code == 503
    failed = await db_session.get(Query, uuid.UUID(refused.value.detail["query_id"]))
    assert (failed.status, failed.origin) == ("failed", "dashboard")
