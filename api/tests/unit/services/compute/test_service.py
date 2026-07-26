import uuid

import pytest
import sqlalchemy as sa
from conftest import seed_workspace

from api.models.agent import Agent
from api.models.user import Credential
from api.services.compute import service

pytestmark = pytest.mark.asyncio


async def _agents(db):
    return (await db.execute(sa.select(Agent))).scalars().all()


async def test_resolve_pool_key_from_backend_kinds(db_session):
    ws, _ = await seed_workspace(db_session, user_id=uuid.uuid4(), backend_kind="object_store")
    assert await service.resolve_pool_key(db_session, ws) == "object_store"


async def test_ensure_agent_provisions_when_none(session_factory, elastic_on, null_backend):
    async with session_factory() as db:
        agent = await service.ensure_agent(db, "object_store")
        assert agent is not None

    async with session_factory() as db:
        rows = await _agents(db)
        assert len(rows) == 1
        row = rows[0]
        assert row.provider == "null"
        assert row.lifecycle == "provisioning"
        assert row.pool_key == "object_store"
        assert row.instance_id and row.instance_id in null_backend._instances
        # A bootstrap credential bound to this row was minted for dial-home.
        cred = (
            await db.execute(
                sa.select(Credential).where(
                    Credential.agent_id == row.id, Credential.kind == "agent_bootstrap"
                )
            )
        ).scalar_one()
        assert cred.token.startswith("dh_boot_")


async def test_ensure_agent_coalesces_under_cap(session_factory, elastic_on):
    """Two asks for the same pool provision exactly one agent (cap = 1)."""
    async with session_factory() as db:
        first = await service.ensure_agent(db, "object_store")
    async with session_factory() as db:
        second = await service.ensure_agent(db, "object_store")
    assert first is not None
    assert second is None
    async with session_factory() as db:
        assert len(await _agents(db)) == 1


async def test_ensure_agent_disabled_is_noop(session_factory, null_backend):
    async with session_factory() as db:
        assert await service.ensure_agent(db, "object_store") is None
        assert await _agents(db) == []


async def test_ensure_agent_provision_failure_marks_failed(
    session_factory, elastic_on, null_backend, monkeypatch
):
    async def boom(_req):
        raise RuntimeError("cloud down")

    monkeypatch.setattr(null_backend, "provision", boom)
    async with session_factory() as db:
        assert await service.ensure_agent(db, "object_store") is None
    async with session_factory() as db:
        row = (await _agents(db))[0]
        assert row.lifecycle == "failed"
        assert row.terminated_at is not None


async def _seed_running_elastic_agent(db, pool_key="object_store"):
    from datetime import UTC, datetime

    agent = Agent(
        name="e",
        status="healthy",
        capabilities={"extensions": ["httpfs"]},
        provider="null",
        lifecycle="running",
        pool_key=pool_key,
        instance_id="dh-bind",
        provisioned_at=datetime.now(tz=UTC),
    )
    db.add(agent)
    await db.flush()
    return agent


async def test_bind_queued_work_dispatches_matching_query(session_factory, elastic_on, monkeypatch):
    """A registering elastic agent picks up a queued query for its pool."""
    from api.models.query import Query
    from api.services.compute import service

    dispatched: list = []

    async def fake_dispatch(db, query, **kwargs):
        dispatched.append(query.id)

    monkeypatch.setattr("api.services.query.dispatch_query", fake_dispatch)

    async with session_factory() as db:
        ws, _ = await seed_workspace(db, user_id=uuid.uuid4())
        agent = await _seed_running_elastic_agent(db)
        db.add(
            Query(
                workspace_id=ws.id,
                agent_id=None,
                sql="SELECT 1",
                status="queued",
                origin="elastic",
            )
        )
        await db.commit()
        bound = await service.bind_queued_work(db, agent)

    assert bound == 1
    assert len(dispatched) == 1


async def test_bind_queued_work_skips_other_pools(session_factory, elastic_on, monkeypatch):
    """A queued query whose workspace needs a different pool is left for another agent."""
    from api.models.query import Query
    from api.services.compute import service

    async def fake_dispatch(db, query, **kwargs):  # pragma: no cover - must not run
        raise AssertionError("should not dispatch a mismatched pool")

    monkeypatch.setattr("api.services.query.dispatch_query", fake_dispatch)

    async with session_factory() as db:
        ws, _ = await seed_workspace(db, user_id=uuid.uuid4(), backend_kind="object_store")
        # Agent serves a different pool than the workspace needs.
        agent = await _seed_running_elastic_agent(db, pool_key="adls")
        db.add(
            Query(
                workspace_id=ws.id,
                agent_id=None,
                sql="SELECT 1",
                status="queued",
                origin="elastic",
            )
        )
        await db.commit()
        bound = await service.bind_queued_work(db, agent)

    assert bound == 0


async def test_record_activity_only_touches_elastic(db_session):
    static = Agent(name="static", status="healthy")
    elastic = Agent(name="e", status="healthy", provider="null", lifecycle="running")
    db_session.add_all([static, elastic])
    await db_session.commit()

    await service.record_activity(db_session, static.id)
    await service.record_activity(db_session, elastic.id)
    await db_session.commit()
    await db_session.refresh(static)
    await db_session.refresh(elastic)

    assert static.last_active_at is None
    assert elastic.last_active_at is not None


async def test_null_backend_reports_no_address(null_backend):
    """Nothing to report: a null instance is reached over the socket it dialed in on,
    exactly like a static agent."""
    assert await null_backend.address("dh-agent-whatever") is None


async def test_resolve_result_host_asks_the_backend(monkeypatch, null_backend):
    """For an elastic agent the control plane created the instance, so the cloud is the
    authority on where it can be reached."""

    async def _address(instance_id):
        assert instance_id == "dh-agent-1"
        return "10.42.3.7"

    monkeypatch.setattr(null_backend, "address", _address)
    agent = Agent(name="e", status="unavailable", provider="null", instance_id="dh-agent-1")

    assert await service.resolve_result_host(agent) == "10.42.3.7"


async def test_resolve_result_host_ignores_static_agents():
    """A static agent's address is whatever it connected from; there is no backend to
    ask, and asking one would be wrong."""
    assert await service.resolve_result_host(Agent(name="s", status="healthy")) is None


async def test_resolve_result_host_survives_a_backend_failure(monkeypatch, null_backend):
    """A transient cloud error must not fail the agent's registration; the caller falls
    back to the connection's peer address."""

    async def _boom(instance_id):
        raise RuntimeError("ARM unavailable")

    monkeypatch.setattr(null_backend, "address", _boom)
    agent = Agent(name="e", status="unavailable", provider="null", instance_id="dh-agent-2")

    assert await service.resolve_result_host(agent) is None


async def test_ensure_result_host_resolves_and_persists(session_factory, monkeypatch, null_backend):
    """Registration can legitimately leave the address unknown, so it is resolved when
    first needed and stored so the next fetch does not pay for it again."""

    async def _address(instance_id):
        return "10.42.3.11"

    monkeypatch.setattr(null_backend, "address", _address)

    async with session_factory() as db:
        agent = Agent(
            name="e", status="healthy", provider="null", instance_id="dh-agent-3", result_host=None
        )
        db.add(agent)
        await db.commit()
        agent_id = agent.id

        assert await service.ensure_result_host(db, agent) == "10.42.3.11"

    async with session_factory() as db:
        assert (await db.get(Agent, agent_id)).result_host == "10.42.3.11"


async def test_ensure_result_host_keeps_a_known_address(session_factory, monkeypatch, null_backend):
    """A host already on the row wins: it may have been advertised by the agent, which
    is more authoritative than cloud metadata."""

    async def _boom(instance_id):
        raise AssertionError("backend must not be consulted when the host is known")

    monkeypatch.setattr(null_backend, "address", _boom)

    async with session_factory() as db:
        agent = Agent(
            name="e",
            status="healthy",
            provider="null",
            instance_id="dh-agent-4",
            result_host="agent.internal",
        )
        db.add(agent)
        await db.commit()

        assert await service.ensure_result_host(db, agent) == "agent.internal"
