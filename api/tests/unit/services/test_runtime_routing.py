"""Runtimes in the dispatch path: which agent is auto-picked, what every dispatch
checks, and what it records.

`dispatch_query` is the one function every query goes through — interactive runs,
the scheduler, maintenance, and runs parked while an agent was starting — so these
pin the checks there rather than in each caller.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import replace

import pytest
from conftest import seed_workspace

from api.models.agent import Agent
from api.models.query import Query
from api.models.user import User
from api.services import query as query_service
from api.services.agent_registry import registry
from api.services.auth import hash_password
from api.services.runtimes import AgentNotDispatchable
from duckhaven_shared import runtimes


class FakeWS:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_text(self, payload: str) -> None:
        self.sent.append(json.loads(payload))


@pytest.fixture(autouse=True)
def _clear_registry():
    yield
    for cid in list(registry.connected_ids()):
        registry.unregister(uuid.UUID(cid))


@pytest.fixture
def beta_runtime(monkeypatch):
    base = runtimes.RUNTIMES["1.5"]
    monkeypatch.setitem(
        runtimes.RUNTIMES,
        "2.0",
        replace(base, id="2.0", display_name="DuckDB 2.0", duckdb_line="2.0", status="beta"),
    )


def _caps(runtime_id: str, engine: str, **extra) -> dict:
    return {
        "duckdb_version": engine.lstrip("v"),
        "engine_version": engine,
        "runtime_id": runtime_id,
        "extensions": ["httpfs", "iceberg"],
        **extra,
    }


async def _workspace(db):
    user = User(email="rt@test.local", password_hash=hash_password("pw"), name="R", role="user")
    db.add(user)
    await db.flush()
    ws, _ = await seed_workspace(db, user_id=user.id, slug="rt-ws", name="RT")
    return ws, user


async def _connected(db, name: str, caps: dict) -> tuple[Agent, FakeWS]:
    agent = Agent(name=name, status="healthy", capabilities=caps)
    db.add(agent)
    await db.flush()
    ws = FakeWS()
    registry.register(agent.id, ws)  # type: ignore[arg-type]
    return agent, ws


async def test_auto_pick_prefers_the_default_runtime_over_a_beta_one(db_session, beta_runtime):
    ws, _ = await _workspace(db_session)
    # The beta agent is inserted first, so it would win a first-match pick.
    await _connected(db_session, "beta", _caps("2.0", "v2.0.1"))
    default, _ = await _connected(db_session, "default", _caps("1.5", "v1.5.5"))
    await db_session.commit()

    picked = await query_service.pick_agent_for(db_session, ws)
    assert picked is not None and picked.id == default.id


async def test_a_beta_runtime_is_never_auto_picked(db_session, beta_runtime):
    """Only work that names a beta agent runs there, even if it is the only one up."""
    ws, _ = await _workspace(db_session)
    await _connected(db_session, "beta", _caps("2.0", "v2.0.1"))
    await db_session.commit()

    assert await query_service.pick_agent_for(db_session, ws) is None


async def test_a_session_auto_pick_skips_an_agent_whose_lock_failed(db_session):
    ws, _ = await _workspace(db_session)
    await _connected(db_session, "unlocked", _caps("1.5", "v1.5.5", sandbox="failed"))
    locked, _ = await _connected(db_session, "locked", _caps("1.5", "v1.5.5", sandbox="verified"))
    await db_session.commit()

    picked = await query_service.pick_agent_for(db_session, ws, for_session=True)
    assert picked is not None and picked.id == locked.id


async def test_dispatch_records_the_runtime_that_ran_the_query(db_session):
    ws, user = await _workspace(db_session)
    agent, sock = await _connected(db_session, "a", _caps("1.5", "v1.5.5"))
    query = Query(workspace_id=ws.id, agent_id=agent.id, user_id=user.id, sql="SELECT 1")
    db_session.add(query)
    await db_session.flush()

    await query_service.dispatch_query(db_session, query)

    assert query.runtime_id == "1.5"
    assert sock.sent[-1]["type"] == "dispatch_query"


async def test_dispatch_refuses_an_agent_on_an_unsupported_runtime(db_session):
    """However a query reached the agent — a schedule naming it, a run parked on its
    restart — nothing is sent to an agent whose runtime isn't trusted with work."""
    ws, user = await _workspace(db_session)
    agent, sock = await _connected(db_session, "custom", _caps("9.9", "v9.9.0"))
    query = Query(workspace_id=ws.id, agent_id=agent.id, user_id=user.id, sql="SELECT 1")
    db_session.add(query)
    await db_session.flush()

    with pytest.raises(AgentNotDispatchable) as exc:
        await query_service.dispatch_query(db_session, query)
    assert exc.value.code == "runtime_unsupported"
    assert sock.sent == []


async def test_dispatch_refuses_an_agent_missing_an_extension(db_session):
    """The extension check used to live only in the interactive router, so a
    scheduled run pinned to an incompatible agent was sent to it anyway."""
    ws, user = await _workspace(db_session)
    caps = _caps("1.5", "v1.5.5")
    caps["extensions"] = ["iceberg"]
    agent, sock = await _connected(db_session, "no-httpfs", caps)
    query = Query(workspace_id=ws.id, agent_id=agent.id, user_id=user.id, sql="SELECT 1")
    db_session.add(query)
    await db_session.flush()

    with pytest.raises(AgentNotDispatchable) as exc:
        await query_service.dispatch_query(db_session, query)
    assert exc.value.code == "agent_incompatible"
    assert sock.sent == []
