import uuid
from datetime import UTC, datetime, timedelta

import pytest
from conftest import seed_workspace

from api.config import settings
from api.models.agent import Agent
from api.models.query import Query
from api.models.sql_session import SqlSession
from api.services.compute import reaper

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _elastic_provider(monkeypatch):
    # The reaper reconciles against the configured provider's backend.
    monkeypatch.setattr(settings, "elastic_provider", "null")


def _ago(seconds: float) -> datetime:
    return datetime.now(tz=UTC) - timedelta(seconds=seconds)


async def _add_running_agent(db, *, instance_id, last_active_s, provisioned_s):
    agent = Agent(
        name="e",
        status="healthy",
        provider="null",
        lifecycle="running",
        pool_key="object_store",
        instance_id=instance_id,
        last_active_at=_ago(last_active_s),
        provisioned_at=_ago(provisioned_s),
    )
    db.add(agent)
    await db.flush()
    return agent


async def _lifecycle(session_factory, agent_id) -> str:
    async with session_factory() as db:
        return (await db.get(Agent, agent_id)).lifecycle


async def test_terminates_idle_agent(session_factory, null_backend, monkeypatch):
    monkeypatch.setattr(settings, "elastic_idle_timeout_s", 900.0)
    async with session_factory() as db:
        agent = await _add_running_agent(
            db, instance_id="dh-idle", last_active_s=1000, provisioned_s=1000
        )
        await db.commit()
        aid = agent.id
    null_backend._instances.add("dh-idle")

    reaped = await reaper.run_cycle(session_factory)

    assert reaped["idle"] == 1
    assert await _lifecycle(session_factory, aid) == "terminated"
    assert "dh-idle" not in null_backend._instances


async def test_keeps_recently_active_agent(session_factory, null_backend, monkeypatch):
    monkeypatch.setattr(settings, "elastic_idle_timeout_s", 900.0)
    async with session_factory() as db:
        agent = await _add_running_agent(
            db, instance_id="dh-fresh", last_active_s=10, provisioned_s=1000
        )
        await db.commit()
        aid = agent.id
    null_backend._instances.add("dh-fresh")

    reaped = await reaper.run_cycle(session_factory)

    assert reaped["idle"] == 0
    assert await _lifecycle(session_factory, aid) == "running"


async def test_idle_agent_with_in_flight_query_is_kept(session_factory, null_backend, monkeypatch):
    monkeypatch.setattr(settings, "elastic_idle_timeout_s", 900.0)
    async with session_factory() as db:
        ws, _ = await seed_workspace(db, user_id=uuid.uuid4())
        agent = await _add_running_agent(
            db, instance_id="dh-busy", last_active_s=1000, provisioned_s=1000
        )
        db.add(Query(workspace_id=ws.id, agent_id=agent.id, sql="SELECT 1", status="running"))
        await db.commit()
        aid = agent.id
    null_backend._instances.add("dh-busy")

    reaped = await reaper.run_cycle(session_factory)

    assert reaped["idle"] == 0
    assert await _lifecycle(session_factory, aid) == "running"


async def test_idle_agent_with_open_session_is_kept(session_factory, null_backend, monkeypatch):
    monkeypatch.setattr(settings, "elastic_idle_timeout_s", 900.0)
    async with session_factory() as db:
        ws, _ = await seed_workspace(db, user_id=uuid.uuid4())
        agent = await _add_running_agent(
            db, instance_id="dh-sess", last_active_s=1000, provisioned_s=1000
        )
        db.add(SqlSession(workspace_id=ws.id, agent_id=agent.id, status="open"))
        await db.commit()
        aid = agent.id
    null_backend._instances.add("dh-sess")

    reaped = await reaper.run_cycle(session_factory)

    assert reaped["idle"] == 0
    assert await _lifecycle(session_factory, aid) == "running"


async def test_max_lifetime_backstop_terminates_drained_agent(
    session_factory, null_backend, monkeypatch
):
    monkeypatch.setattr(settings, "elastic_idle_timeout_s", 900.0)
    monkeypatch.setattr(settings, "elastic_max_lifetime_s", 3600.0)
    async with session_factory() as db:
        # Recently active (not idle) but past max lifetime, no in-flight work.
        agent = await _add_running_agent(
            db, instance_id="dh-old", last_active_s=10, provisioned_s=5000
        )
        await db.commit()
        aid = agent.id
    null_backend._instances.add("dh-old")

    reaped = await reaper.run_cycle(session_factory)

    assert reaped["max_lifetime"] == 1
    assert await _lifecycle(session_factory, aid) == "terminated"


async def test_fails_stuck_provisioning_agent(session_factory, null_backend, monkeypatch):
    monkeypatch.setattr(settings, "elastic_provisioning_deadline_s", 300.0)
    async with session_factory() as db:
        agent = Agent(
            name="e",
            status="unavailable",
            provider="null",
            lifecycle="provisioning",
            pool_key="object_store",
            instance_id="dh-stuck",
            provisioned_at=_ago(1000),
        )
        db.add(agent)
        await db.commit()
        aid = agent.id
    null_backend._instances.add("dh-stuck")

    reaped = await reaper.run_cycle(session_factory)

    assert reaped["provisioning_timeout"] == 1
    assert await _lifecycle(session_factory, aid) == "failed"
    assert "dh-stuck" not in null_backend._instances


async def test_recent_provisioning_agent_is_kept(session_factory, null_backend, monkeypatch):
    monkeypatch.setattr(settings, "elastic_provisioning_deadline_s", 300.0)
    async with session_factory() as db:
        agent = Agent(
            name="e",
            status="unavailable",
            provider="null",
            lifecycle="provisioning",
            pool_key="object_store",
            instance_id="dh-new",
            provisioned_at=_ago(5),
        )
        db.add(agent)
        await db.commit()
        aid = agent.id
    null_backend._instances.add("dh-new")

    reaped = await reaper.run_cycle(session_factory)

    assert reaped["provisioning_timeout"] == 0
    assert await _lifecycle(session_factory, aid) == "provisioning"


async def test_reconcile_terminates_orphan_instance(session_factory, null_backend):
    # An instance in the cloud with no backing row is a leak → terminate it.
    null_backend._instances.add("dh-orphan")

    reaped = await reaper.run_cycle(session_factory)

    assert reaped["orphans_terminated"] == 1
    assert "dh-orphan" not in null_backend._instances


async def test_reconcile_fails_row_whose_instance_vanished(
    session_factory, null_backend, monkeypatch
):
    # A running row whose instance is gone from the cloud → fail the row.
    monkeypatch.setattr(settings, "elastic_idle_timeout_s", 900.0)
    async with session_factory() as db:
        agent = await _add_running_agent(
            db, instance_id="dh-vanished", last_active_s=10, provisioned_s=20
        )
        await db.commit()
        aid = agent.id
    # Deliberately do NOT add the instance to the backend.

    reaped = await reaper.run_cycle(session_factory)

    assert reaped["dead_rows_failed"] == 1
    assert await _lifecycle(session_factory, aid) == "failed"


async def test_run_tick_runs_when_leader(session_factory, null_backend):
    result = await reaper.run_tick(session_factory)
    assert result is not None


async def test_leak_sweep_leaves_a_still_provisioning_agent_alone(
    session_factory, elastic_on, null_backend
):
    """The row is committed with its instance_id before the backend is asked to create
    the instance, so there is a window where the instance legitimately does not exist
    yet. A cycle landing inside it used to fail a healthy agent, and then terminate its
    instance as an orphan on the next pass because the row was no longer active."""
    from datetime import UTC, datetime

    async with session_factory() as db:
        agent = Agent(
            name="mid-create",
            status="unavailable",
            provider="null",
            lifecycle="provisioning",
            instance_id="dh-agent-notyet",
            provisioned_at=datetime.now(tz=UTC),
        )
        db.add(agent)
        await db.commit()
        agent_id = agent.id

    # The backend knows nothing about it yet, exactly as during the create call.
    assert "dh-agent-notyet" not in await null_backend.list_managed()

    result = await reaper.run_cycle(session_factory)
    assert result["dead_rows_failed"] == 0

    async with session_factory() as db:
        assert (await db.get(Agent, agent_id)).lifecycle == "provisioning"


async def test_leak_sweep_still_fails_a_provisioning_row_past_its_deadline(
    session_factory, elastic_on, null_backend, monkeypatch
):
    """The grace period must not become a permanent exemption. Past the deadline a
    provisioning row with no backing instance is failed like any other."""
    from datetime import UTC, datetime, timedelta

    from api.config import settings

    monkeypatch.setattr(settings, "elastic_provisioning_deadline_s", 300.0)

    async with session_factory() as db:
        agent = Agent(
            name="never-created",
            status="unavailable",
            provider="null",
            lifecycle="provisioning",
            instance_id="dh-agent-never",
            provisioned_at=datetime.now(tz=UTC) - timedelta(seconds=3600),
        )
        db.add(agent)
        await db.commit()
        agent_id = agent.id

    await reaper.run_cycle(session_factory)

    async with session_factory() as db:
        # _reap_lifecycle gets there first at the deadline; either way it is failed.
        assert (await db.get(Agent, agent_id)).lifecycle == "failed"


async def test_stranded_queued_pool_run_is_failed(session_factory, elastic_on, monkeypatch):
    """A pool run is parked queued with no agent and only leaves that state when a
    provisioned agent binds it. If the cap was already reached, provisioning failed, or
    the agent was failed at its deadline, nothing ever touches the run -- it stayed
    queued forever and the client polled an answer that was never coming."""
    from datetime import UTC, datetime, timedelta

    from api.config import settings
    from api.models.query import Query
    from api.models.workspace import Workspace

    monkeypatch.setattr(settings, "elastic_provisioning_deadline_s", 300.0)

    async with session_factory() as db:
        ws = Workspace(slug="stranded-ws", name="Stranded")
        db.add(ws)
        await db.flush()
        old = Query(
            workspace_id=ws.id,
            sql="SELECT 1",
            status="queued",
            origin="elastic",
            started_at=datetime.now(tz=UTC) - timedelta(seconds=3600),
        )
        fresh = Query(
            workspace_id=ws.id,
            sql="SELECT 2",
            status="queued",
            origin="elastic",
            started_at=datetime.now(tz=UTC),
        )
        db.add_all([old, fresh])
        await db.commit()
        old_id, fresh_id = old.id, fresh.id

    result = await reaper.run_cycle(session_factory)
    assert result["stranded_queries_failed"] == 1

    async with session_factory() as db:
        failed = await db.get(Query, old_id)
        assert failed.status == "failed"
        assert "No compute" in failed.error
        assert failed.finished_at is not None
        # Still inside its budget: supply may yet arrive.
        assert (await db.get(Query, fresh_id)).status == "queued"
