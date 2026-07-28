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


async def test_terminate_agent_gives_up_presence(session_factory, elastic_on, null_backend):
    """Deleting a container group is not instant, and until it completes the agent keeps
    heartbeating -- refreshing last_ping_at and re-asserting healthy. Presence is read
    from those columns, so a terminated agent that keeps its ownership row stays
    selectable by the picker and queries get dispatched into a dying container."""
    from datetime import UTC, datetime

    from api.services.agent_dispatch import connected_agent_ids

    async with session_factory() as db:
        agent = Agent(
            name="e",
            status="healthy",
            provider="null",
            lifecycle="running",
            instance_id="dh-agent-term",
            owner_id="api",
            owner_url="http://10.0.0.1:8000",
            last_ping_at=datetime.now(tz=UTC),
        )
        db.add(agent)
        await db.commit()
        agent_id = agent.id
        assert str(agent_id) in await connected_agent_ids(db)

        await service.terminate_agent(db, agent, reason="test")

    async with session_factory() as db:
        row = await db.get(Agent, agent_id)
        assert row.lifecycle == "terminated"
        assert row.status == "unavailable"
        assert row.owner_url is None
        assert row.owner_id is None
        # The point of the fix: no longer advertised as connected anywhere.
        assert str(agent_id) not in await connected_agent_ids(db)


async def test_bind_queued_work_replays_the_requested_catalog_and_timeout(
    session_factory, elastic_on, null_backend, monkeypatch
):
    """A run parked during a cold start is dispatched from here, outside the request that
    created it, so the catalog and timeout the user chose have to come off the row. Losing
    the catalog meant unqualified table names resolved against the workspace default
    instead of the worksheet's selection."""
    import uuid as _uuid

    from conftest import seed_workspace

    from api.models.query import Query

    captured: dict = {}

    async def _fake_dispatch(db, query, **kwargs):
        captured.update(kwargs)

    monkeypatch.setattr("api.services.query.dispatch_query", _fake_dispatch)

    async with session_factory() as db:
        ws, _ = await seed_workspace(db, user_id=_uuid.uuid4(), backend_kind="object_store")
        agent = Agent(
            name="e",
            status="healthy",
            provider="null",
            lifecycle="running",
            pool_key="object_store",
            instance_id="dh-agent-bind",
        )
        db.add(agent)
        db.add(
            Query(
                workspace_id=ws.id,
                sql="SELECT 1",
                status="queued",
                origin="elastic",
                timeout_s=42.0,
                active_catalog="sales",
            )
        )
        await db.commit()

        assert await service.bind_queued_work(db, agent) == 1

    assert captured["active_catalog"] == "sales"
    assert captured["timeout_s"] == 42.0


async def test_bind_queued_work_omits_an_unrecorded_timeout(
    session_factory, elastic_on, null_backend, monkeypatch
):
    """Rows written before the timeout was recorded must keep the dispatch default rather
    than being handed None."""
    import uuid as _uuid

    from conftest import seed_workspace

    from api.models.query import Query

    captured: dict = {}

    async def _fake_dispatch(db, query, **kwargs):
        captured.update(kwargs)

    monkeypatch.setattr("api.services.query.dispatch_query", _fake_dispatch)

    async with session_factory() as db:
        ws, _ = await seed_workspace(db, user_id=_uuid.uuid4(), backend_kind="object_store")
        agent = Agent(
            name="e",
            status="healthy",
            provider="null",
            lifecycle="running",
            pool_key="object_store",
            instance_id="dh-agent-bind2",
        )
        db.add(agent)
        db.add(Query(workspace_id=ws.id, sql="SELECT 1", status="queued", origin="elastic"))
        await db.commit()

        assert await service.bind_queued_work(db, agent) == 1

    assert "timeout_s" not in captured
    assert captured["active_catalog"] is None


async def test_concurrent_binds_dispatch_a_queued_query_once(
    session_factory, elastic_on, monkeypatch
):
    """Two agents registering into the same pool must not both run the same query.

    bind_queued_work selects `agent_id IS NULL` rows and only commits after the
    whole loop, so two concurrent callers see the same parked work and both
    dispatch it. Dispatch is the irreversible half: a parked
    `INSERT INTO ... SELECT` executes twice and duplicates Iceberg data, while
    only the last write to `agent_id` is recorded.

    Reachable at the default cap of one agent per pool. `ensure_agent` takes a
    per-pool advisory lock before its check-then-provision, but
    `restart_elastic_agent` performs no cap check at all, and bind_queued_work
    re-runs on every socket registration — so a reconnect during a restart is
    enough.
    """
    import asyncio

    from api.models.query import Query
    from api.services.compute import service

    dispatched: list = []
    gate = asyncio.Event()

    async def fake_dispatch(db, query, **kwargs):
        # Hold both callers inside the loop, after they have selected the row and
        # before either commits — the window the missing lock leaves open.
        dispatched.append(query.id)
        await gate.wait()

    monkeypatch.setattr("api.services.query.dispatch_query", fake_dispatch)

    async with session_factory() as db:
        ws, _ = await seed_workspace(db, user_id=uuid.uuid4())
        first = await _seed_running_elastic_agent(db)
        second = Agent(
            name="e2",
            status="healthy",
            capabilities={"extensions": ["httpfs"]},
            provider="null",
            lifecycle="running",
            pool_key="object_store",
            instance_id="dh-bind-2",
            provisioned_at=first.provisioned_at,
        )
        db.add(second)
        db.add(
            Query(
                workspace_id=ws.id,
                agent_id=None,
                sql="INSERT INTO t SELECT * FROM s",
                status="queued",
                origin="elastic",
            )
        )
        await db.commit()
        first_id, second_id = first.id, second.id

    async with session_factory() as db_a, session_factory() as db_b:
        agent_a = await db_a.get(Agent, first_id)
        agent_b = await db_b.get(Agent, second_id)
        task_a = asyncio.create_task(service.bind_queued_work(db_a, agent_a))
        task_b = asyncio.create_task(service.bind_queued_work(db_b, agent_b))
        # Let both reach the gate (or finish, once the claim is atomic).
        for _ in range(20):
            await asyncio.sleep(0)
        gate.set()
        await asyncio.gather(task_a, task_b)

    assert len(dispatched) == 1, f"the same queued query was dispatched {len(dispatched)} times"


async def test_failed_dispatch_releases_the_claim(session_factory, elastic_on, monkeypatch):
    """An agent that could not run the query must not stay recorded against it.

    agent_id is the audit trail and backs the History agent filter, so leaving it
    set attributes a run to an agent that never executed it — permanently, since
    nothing revisits a failed row.
    """
    from api.models.query import Query
    from api.services.compute import service

    async def failing_dispatch(db, query, **kwargs):
        raise RuntimeError("agent went away")

    monkeypatch.setattr("api.services.query.dispatch_query", failing_dispatch)

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
        assert await service.bind_queued_work(db, agent) == 0

    async with session_factory() as db:
        query = (await db.execute(sa.select(Query))).scalars().one()
        assert query.status == "failed"
        assert query.agent_id is None, "a run this agent never executed is attributed to it"
