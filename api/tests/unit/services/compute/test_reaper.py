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


async def test_reconciles_a_provider_no_longer_configured(
    session_factory, elastic_on, null_backend, monkeypatch
):
    """Reconciling only the configured provider strands instances the moment an operator
    changes it: the previous provider's orphans are never enumerated again, so they are
    never terminated and keep billing. The provider set comes from the rows, not the
    setting."""
    from datetime import UTC, datetime, timedelta

    from api.config import settings

    monkeypatch.setattr(settings, "elastic_provisioning_deadline_s", 300.0)

    async with session_factory() as db:
        agent = Agent(
            name="left-over",
            status="healthy",
            provider="null",
            lifecycle="running",
            instance_id="dh-agent-leftover",
            provisioned_at=datetime.now(tz=UTC) - timedelta(seconds=3600),
            last_active_at=datetime.now(tz=UTC),
        )
        db.add(agent)
        await db.commit()
        agent_id = agent.id

    # The operator has since pointed the deployment somewhere else.
    monkeypatch.setattr(settings, "elastic_provider", "azure_aci")

    result = await reaper.run_cycle(session_factory)

    # The null-backed row is still reconciled: its instance is gone, so it is failed.
    assert result["dead_rows_failed"] == 1
    async with session_factory() as db:
        assert (await db.get(Agent, agent_id)).lifecycle == "failed"


async def test_one_broken_provider_does_not_stop_the_others(
    session_factory, elastic_on, null_backend, monkeypatch
):
    """A provider left over from an earlier configuration is exactly the case where its
    settings may be gone, so its backend can fail to build. That must not prevent the
    remaining providers being reconciled."""
    from datetime import UTC, datetime, timedelta

    from api.config import settings

    monkeypatch.setattr(settings, "elastic_provisioning_deadline_s", 300.0)
    # azure_aci with no subscription configured raises when asked to do anything.
    monkeypatch.setattr(settings, "elastic_azure_subscription_id", None)

    async with session_factory() as db:
        db.add(
            Agent(
                name="broken-provider",
                status="healthy",
                provider="azure_aci",
                lifecycle="running",
                instance_id="dh-agent-broken",
                provisioned_at=datetime.now(tz=UTC) - timedelta(seconds=3600),
                last_active_at=datetime.now(tz=UTC),
            )
        )
        good = Agent(
            name="reconcilable",
            status="healthy",
            provider="null",
            lifecycle="running",
            instance_id="dh-agent-good",
            provisioned_at=datetime.now(tz=UTC) - timedelta(seconds=3600),
            last_active_at=datetime.now(tz=UTC),
        )
        db.add(good)
        await db.commit()
        good_id = good.id

    result = await reaper.run_cycle(session_factory)

    assert result["dead_rows_failed"] == 1
    async with session_factory() as db:
        assert (await db.get(Agent, good_id)).lifecycle == "failed"


async def test_failing_a_stuck_agent_revokes_its_bootstrap_credential(
    session_factory, null_backend, monkeypatch
):
    """A failed row's enrollment token must not outlive it.

    The credential is single-use and is consumed on successful registration, so
    nothing else ever collects it. An agent that never dials home leaves a token
    valid for BOOTSTRAP_TTL_HOURS behind, and every Restart mints another — so a
    row that has failed a few times carries several live enrollment secrets, any
    of which still registers an agent.
    """
    from sqlalchemy import func, select

    from api.models.user import Credential

    monkeypatch.setattr(settings, "elastic_provisioning_deadline_s", 300.0)
    async with session_factory() as db:
        agent = Agent(
            name="e",
            status="unavailable",
            provider="null",
            lifecycle="provisioning",
            pool_key="object_store",
            instance_id="dh-stuck-cred",
            provisioned_at=_ago(1000),
        )
        db.add(agent)
        await db.flush()
        db.add(
            Credential(
                agent_id=agent.id,
                kind="agent_bootstrap",
                token="dh_boot_orphaned",
                expires_at=datetime.now(tz=UTC) + timedelta(hours=24),
            )
        )
        await db.commit()
        aid = agent.id
    null_backend._instances.add("dh-stuck-cred")

    await reaper.run_cycle(session_factory)

    async with session_factory() as db:
        assert (await db.get(Agent, aid)).lifecycle == "failed"
        live = (
            await db.execute(
                select(func.count())
                .select_from(Credential)
                .where(Credential.agent_id == aid, Credential.kind == "agent_bootstrap")
            )
        ).scalar_one()
        assert live == 0, "a failed agent's bootstrap token is still valid for 24h"


class _LazyDeleteBackend:
    """A backend whose delete is asynchronous, like Azure Container Instances.

    `begin_delete` returns immediately and the group takes tens of seconds to
    disappear, so an instance stays enumerable well after its row is terminated.
    """

    provider = "null"

    def __init__(self) -> None:
        self._instances: set[str] = set()
        self.terminate_calls: list[str] = []

    async def provision(self, req) -> str:  # pragma: no cover - unused here
        self._instances.add(req.instance_id)
        return req.instance_id

    async def terminate(self, instance_id: str) -> None:
        self.terminate_calls.append(instance_id)  # accepted, but not yet gone

    async def address(self, instance_id: str) -> str | None:
        return None

    async def status(self, instance_id: str) -> str:
        return "running" if instance_id in self._instances else "gone"

    async def list_managed(self) -> set[str]:
        return set(self._instances)


@pytest.fixture
def lazy_backend(monkeypatch):
    from api.services.compute import backends

    backend = _LazyDeleteBackend()
    monkeypatch.setitem(backends._BACKENDS, "null", backend)
    return backend


async def test_normal_scale_in_is_not_reported_as_a_leak(
    session_factory, lazy_backend, monkeypatch
):
    """An instance still being deleted is not an orphan.

    `terminate_agent` marks the row terminated and the delete completes later, so
    the instance is still enumerable on the same cycle. Building `known` from
    active rows only made every routine scale-in land in `live - known`: it was
    deleted twice and logged `Terminated orphan instance`, which is the signal an
    operator would watch for a real leak.
    """
    monkeypatch.setattr(settings, "elastic_idle_timeout_s", 60.0)
    async with session_factory() as db:
        agent = await _add_running_agent(
            db, instance_id="dh-scaling-in", last_active_s=600, provisioned_s=600
        )
        await db.commit()
        aid = agent.id
    lazy_backend._instances.add("dh-scaling-in")

    reaped = await reaper.run_cycle(session_factory)

    assert reaped["idle"] == 1
    assert await _lifecycle(session_factory, aid) == "terminated"
    assert reaped["orphans_terminated"] == 0, "a routine scale-in was reported as a leak"


async def test_genuine_orphan_is_still_swept(session_factory, lazy_backend):
    """The counterpart: an instance with no row at all is a real leak."""
    lazy_backend._instances.add("dh-nobody-owns-me")

    reaped = await reaper.run_cycle(session_factory)

    assert reaped["orphans_terminated"] == 1
    assert "dh-nobody-owns-me" in lazy_backend.terminate_calls


async def test_stuck_terminating_row_is_finished(session_factory, lazy_backend):
    """`terminating` must not be a dead end.

    terminate_agent commits `terminating`, then calls the backend and closes the
    socket before committing `terminated`. An interruption in that window strands
    the row: no reaper path selects it (both filter on provisioning|running), and
    both admin routes reject it (terminate needs provisioning|running, restart
    needs terminated|failed). Only DELETE could clear it.
    """
    async with session_factory() as db:
        agent = Agent(
            name="e",
            status="unavailable",
            provider="null",
            lifecycle="terminating",
            pool_key="object_store",
            instance_id="dh-interrupted",
            provisioned_at=_ago(600),
        )
        db.add(agent)
        await db.commit()
        aid = agent.id
    # Its instance is already gone; nothing is left to do but finish the row.

    await reaper.run_cycle(session_factory)

    assert await _lifecycle(session_factory, aid) == "terminated"
