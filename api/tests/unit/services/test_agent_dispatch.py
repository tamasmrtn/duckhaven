"""Cross-replica agent presence + dispatch routing."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker

from api.config import settings
from api.models.agent import Agent
from api.services import agent_dispatch
from api.services.agent_dispatch import (
    claim_agent_owner,
    connected_agent_ids,
    drain_local_agents,
    is_agent_connected,
    release_agent_owner,
    send_to_agent,
)
from api.services.agent_registry import registry


class FakeWS:
    def __init__(self) -> None:
        self.sent: list[str] = []
        self.closed = False

    async def send_text(self, payload: str) -> None:
        self.sent.append(payload)

    async def close(self, code: int = 1000) -> None:
        self.closed = True


@pytest.fixture(autouse=True)
def _clean_registry():
    registry._connections.clear()
    yield
    registry._connections.clear()


async def _seed_agent(db, **kwargs) -> Agent:
    agent = Agent(name="a", status="unavailable", **kwargs)
    db.add(agent)
    await db.commit()
    await db.refresh(agent)
    return agent


@pytest_asyncio.fixture
def _own_replica(monkeypatch):
    monkeypatch.setattr(settings, "replica_internal_url", "http://me:8000")
    monkeypatch.setattr(settings, "replica_id", "me")


async def test_claim_and_release_owner(db_session, _own_replica):
    agent = await _seed_agent(db_session)
    await claim_agent_owner(db_session, agent.id)
    await db_session.refresh(agent)
    assert agent.owner_url == "http://me:8000"
    assert agent.owner_id == "me"
    assert agent.status == "healthy"
    assert agent.last_ping_at is not None

    await release_agent_owner(db_session, agent.id)
    await db_session.refresh(agent)
    assert agent.owner_url is None and agent.owner_id is None
    assert agent.status == "unavailable"


async def test_connected_ids_includes_fresh_excludes_stale(db_session):
    fresh = await _seed_agent(
        db_session, owner_url="http://peer:8000", last_ping_at=datetime.now(tz=UTC)
    )
    stale = await _seed_agent(
        db_session,
        owner_url="http://dead:8000",
        last_ping_at=datetime.now(tz=UTC) - timedelta(seconds=settings.agent_presence_ttl_s + 60),
    )
    ids = await connected_agent_ids(db_session)
    assert str(fresh.id) in ids
    assert str(stale.id) not in ids


async def test_connected_ids_unions_local_sockets(db_session):
    """A socket held locally counts even before its ownership row lands."""
    agent = await _seed_agent(db_session)
    registry.register(agent.id, FakeWS())
    assert str(agent.id) in await connected_agent_ids(db_session)


async def test_is_agent_connected_local_shortcircuit(db_session):
    agent_id = uuid.uuid4()
    registry.register(agent_id, FakeWS())
    assert await is_agent_connected(db_session, agent_id) is True


async def test_send_to_agent_local_uses_socket(db_session):
    agent_id = uuid.uuid4()
    ws = FakeWS()
    registry.register(agent_id, ws)
    assert await send_to_agent(db_session, agent_id, "frame") is True
    assert ws.sent == ["frame"]


async def test_send_to_agent_unknown_returns_false(db_session):
    assert await send_to_agent(db_session, uuid.uuid4(), "frame") is False


async def test_send_to_agent_forwards_to_owner(db_session, monkeypatch, _own_replica):
    monkeypatch.setattr(settings, "internal_api_secret", "shared")
    agent = await _seed_agent(
        db_session, owner_url="http://peer:8000", last_ping_at=datetime.now(tz=UTC)
    )

    captured = {}

    class FakeResp:
        status_code = 200

        def json(self):
            return {"delivered": True}

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json, headers):
            captured["url"] = url
            captured["headers"] = headers
            return FakeResp()

    monkeypatch.setattr(agent_dispatch.httpx, "AsyncClient", FakeClient)
    assert await send_to_agent(db_session, agent.id, "frame") is True
    assert captured["url"] == f"http://peer:8000/internal/agents/{agent.id}/send"
    assert captured["headers"]["X-Internal-Secret"] == "shared"


async def test_send_to_agent_forward_disabled_without_secret(db_session, monkeypatch, _own_replica):
    monkeypatch.setattr(settings, "internal_api_secret", None)
    agent = await _seed_agent(
        db_session, owner_url="http://peer:8000", last_ping_at=datetime.now(tz=UTC)
    )
    assert await send_to_agent(db_session, agent.id, "frame") is False


async def test_drain_local_agents_closes_and_releases(db_session, db_engine, _own_replica):
    """Graceful shutdown closes local sockets and clears their ownership so other
    replicas take over."""
    agent = await _seed_agent(
        db_session, owner_url=settings.replica_internal_url, last_ping_at=datetime.now(tz=UTC)
    )
    ws = FakeWS()
    registry.register(agent.id, ws)

    await drain_local_agents(async_sessionmaker(db_engine, expire_on_commit=False))

    assert ws.closed is True
    assert registry.get(agent.id) is None
    await db_session.refresh(agent)
    assert agent.owner_url is None
    assert agent.status == "unavailable"
