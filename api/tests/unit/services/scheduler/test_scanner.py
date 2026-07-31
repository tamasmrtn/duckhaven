from __future__ import annotations

import contextlib
import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from conftest import seed_workspace
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from api.models.agent import Agent
from api.models.agent_grant import AgentGrant
from api.models.query import Query, SavedQuery, Schedule
from api.models.user import User
from api.services.agent_registry import registry
from api.services.auth import hash_password
from api.services.scheduler import scanner as scheduler_mod
from api.services.scheduler.scanner import run_cycle, run_tick, scheduler_leadership


class FakeWS:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_text(self, payload: str) -> None:
        self.sent.append(json.loads(payload))


@pytest_asyncio.fixture
def session_factory(db_engine):
    return async_sessionmaker(db_engine, expire_on_commit=False)


@pytest.fixture(autouse=True)
def _clear_registry():
    yield
    for cid in list(registry.connected_ids()):
        registry.unregister(uuid.UUID(cid))


async def _seed(
    db,
    *,
    connect_agent: bool = True,
    enabled: bool = True,
    next_run_at: datetime | None = None,
    schedule_agent_id: uuid.UUID | None = None,
    default_agent_id: uuid.UUID | None = None,
) -> tuple[Schedule, Agent | None, FakeWS | None]:
    """Seed a workspace, a saved query, and a schedule. Optionally connect an agent."""
    suffix = uuid.uuid4().hex[:8]
    user = User(
        email=f"s-{suffix}@test.local", password_hash=hash_password("pw"), name="S", role="user"
    )
    db.add(user)
    await db.flush()
    ws, _catalog = await seed_workspace(
        db, user_id=user.id, slug=f"sched-ws-{suffix}", name="Sched"
    )

    agent = None
    ws_obj = None
    if connect_agent:
        agent = Agent(
            name="a", status="healthy", capabilities={"extensions": ["httpfs", "iceberg"]}
        )
        db.add(agent)
        await db.flush()
        ws_obj = FakeWS()
        registry.register(agent.id, ws_obj)  # type: ignore[arg-type]

    saved = SavedQuery(
        workspace_id=ws.id,
        name="nightly",
        sql="SELECT 42",
        default_agent_id=default_agent_id,
        created_by=user.id,
    )
    db.add(saved)
    await db.flush()

    schedule = Schedule(
        workspace_id=ws.id,
        job_type="saved_query",
        saved_query_id=saved.id,
        agent_id=schedule_agent_id,
        cron="0 2 * * *",
        enabled=enabled,
        next_run_at=next_run_at,
        created_by=user.id,
    )
    db.add(schedule)
    await db.commit()
    await db.refresh(schedule)
    return schedule, agent, ws_obj


_PAST = datetime(2026, 6, 28, 12, 0, tzinfo=UTC)
_NOW = datetime(2026, 6, 29, 12, 0, tzinfo=UTC)
# SQLite stores datetimes naive; the next 02:00 after _NOW, tz-stripped for compare.
_NEXT = datetime(2026, 6, 30, 2, 0)


def _naive(value: datetime | None) -> datetime | None:
    return value.replace(tzinfo=None) if value is not None else None


async def test_dispatches_due_schedule(session_factory):
    async with session_factory() as db:
        schedule, agent, ws_obj = await _seed(db, next_run_at=_PAST)

    result = await run_cycle(session_factory, now=_NOW)
    assert result == {"status": "ran", "due": 1, "dispatched": 1, "skipped": 0}

    # A scheduled run was recorded and a dispatch frame was sent to the agent.
    async with session_factory() as db:
        q = (await db.execute(select(Query).where(Query.origin == "scheduled"))).scalar_one()
        assert q.sql == "SELECT 42"
        assert q.schedule_id == schedule.id
        assert q.agent_id == agent.id
        sched = await db.get(Schedule, schedule.id)
        assert _naive(sched.last_run_at) == _NOW.replace(tzinfo=None)
        assert sched.last_run_query_id == q.id
        # next_run_at advanced to the next 02:00 after _NOW.
        assert _naive(sched.next_run_at) == _NEXT
    assert ws_obj is not None
    assert ws_obj.sent[-1]["type"] == "dispatch_query"


async def _seed_scoped(db, *, sql: str, grant_tier: str | None):
    """Seed a scoped-catalog schedule whose saved query is `sql`, optionally with
    a grant for the creator at `grant_tier` on analytics.secret."""
    from sqlalchemy import update

    from api.models.catalog import WorkspaceCatalog
    from api.models.catalog_grant import CatalogGrant

    suffix = uuid.uuid4().hex[:8]
    user = User(
        email=f"sc-{suffix}@test.local", password_hash=hash_password("pw"), name="SC", role="user"
    )
    db.add(user)
    await db.flush()
    ws, cat = await seed_workspace(db, user_id=user.id, slug=f"scoped-{suffix}", name="Scoped")
    await db.execute(
        update(WorkspaceCatalog)
        .where(WorkspaceCatalog.workspace_id == ws.id, WorkspaceCatalog.catalog_id == cat.id)
        .values(access_mode="scoped")
    )
    if grant_tier is not None:
        db.add(
            CatalogGrant(
                user_id=user.id,
                catalog_id=cat.id,
                schema_name="analytics",
                table_name="secret",
                tier=grant_tier,
            )
        )
    agent = Agent(name="a", status="healthy", capabilities={"extensions": ["httpfs", "iceberg"]})
    db.add(agent)
    await db.flush()
    registry.register(agent.id, FakeWS())  # type: ignore[arg-type]
    saved = SavedQuery(workspace_id=ws.id, name="nightly", sql=sql, created_by=user.id)
    db.add(saved)
    await db.flush()
    schedule = Schedule(
        workspace_id=ws.id,
        job_type="saved_query",
        saved_query_id=saved.id,
        cron="0 2 * * *",
        enabled=True,
        next_run_at=_PAST,
        created_by=user.id,
    )
    db.add(schedule)
    await db.commit()
    return ws


async def test_scheduled_run_denied_by_creator_grants(session_factory):
    """A scheduled query against a scoped catalog is enforced against the saved
    query's creator; without a grant the run is recorded as failed."""
    async with session_factory() as db:
        ws = await _seed_scoped(db, sql="SELECT * FROM analytics.secret", grant_tier=None)

    await run_cycle(session_factory, now=_NOW)

    async with session_factory() as db:
        q = (
            await db.execute(
                select(Query).where(Query.workspace_id == ws.id, Query.origin == "scheduled")
            )
        ).scalar_one()
        assert q.status == "failed"
        assert "authorized" in (q.error or "").lower()


async def test_scheduled_run_allowed_with_creator_grant(session_factory):
    async with session_factory() as db:
        ws = await _seed_scoped(db, sql="SELECT * FROM analytics.secret", grant_tier="reader")

    await run_cycle(session_factory, now=_NOW)

    async with session_factory() as db:
        q = (
            await db.execute(
                select(Query).where(Query.workspace_id == ws.id, Query.origin == "scheduled")
            )
        ).scalar_one()
        assert q.status == "queued"


async def test_skips_disabled_and_future_schedules(session_factory):
    async with session_factory() as db:
        await _seed(db, enabled=False, next_run_at=_PAST)
    assert (await run_cycle(session_factory, now=_NOW))["due"] == 0

    async with session_factory() as db:
        await _seed(db, next_run_at=_NOW + timedelta(hours=1))
    assert (await run_cycle(session_factory, now=_NOW))["due"] == 0


async def test_schedule_agent_wins_over_default(session_factory):
    """Resolution order: schedule.agent_id beats saved_query.default_agent_id."""
    async with session_factory() as db:
        # Connect the schedule's chosen agent; give the saved query a different default.
        schedule, chosen, _ws = await _seed(db, next_run_at=_PAST)
        other = Agent(name="other", status="healthy", capabilities={"extensions": []})
        db.add(other)
        await db.flush()
        schedule.agent_id = chosen.id
        saved = await db.get(SavedQuery, schedule.saved_query_id)
        saved.default_agent_id = other.id
        await db.commit()
        chosen_id = chosen.id

    await run_cycle(session_factory, now=_NOW)
    async with session_factory() as db:
        q = (await db.execute(select(Query).where(Query.origin == "scheduled"))).scalar_one()
        assert q.agent_id == chosen_id


# --- per-agent access is re-checked on every fire ----------------------------


async def _fail_reason(session_factory) -> str | None:
    async with session_factory() as db:
        q = (await db.execute(select(Query).where(Query.origin == "scheduled"))).scalar_one()
        assert q.status == "failed"
        return q.error


async def test_revoking_access_fails_the_run_without_disabling_the_schedule(session_factory):
    """Access is re-evaluated at dispatch against `schedule.created_by`, not
    snapshotted at creation — otherwise a revoked grant would keep running work
    forever. The run fails visibly; the schedule stays enabled so re-granting
    resumes it with no operator action."""
    async with session_factory() as db:
        schedule, chosen, _ws = await _seed(db, next_run_at=_PAST)
        schedule.agent_id = chosen.id
        # The owner held access when they chose it; the agent is locked down after.
        chosen.access_mode = "restricted"
        await db.commit()
        schedule_id = schedule.id

    assert (await run_cycle(session_factory, now=_NOW))["dispatched"] == 1
    assert "no longer has access" in (await _fail_reason(session_factory))

    async with session_factory() as db:
        after = await db.get(Schedule, schedule_id)
        assert after.enabled is True
        assert after.next_run_at is not None  # still scheduled to try again


async def test_regranting_access_resumes_the_schedule(session_factory):
    async with session_factory() as db:
        schedule, chosen, _ws = await _seed(db, next_run_at=_PAST)
        schedule.agent_id = chosen.id
        chosen.access_mode = "restricted"
        await db.commit()

    await run_cycle(session_factory, now=_NOW)
    assert "no longer has access" in (await _fail_reason(session_factory))

    async with session_factory() as db:
        fresh = await db.get(Schedule, schedule.id)
        db.add(AgentGrant(agent_id=fresh.agent_id, user_id=fresh.created_by, tier="use"))
        fresh.next_run_at = _PAST
        await db.commit()

    await run_cycle(session_factory, now=_NOW + timedelta(minutes=1))
    async with session_factory() as db:
        runs = (await db.execute(select(Query).where(Query.origin == "scheduled"))).scalars().all()
        # Two runs: the one that failed while revoked, and one that dispatched after
        # the re-grant. (`started_at` ties at SQLite's precision, so assert on the
        # set rather than on "the latest".)
        assert len(runs) == 2
        assert [r.status for r in runs].count("failed") == 1


async def test_revoking_access_to_the_saved_query_default_also_fails(session_factory):
    """`saved_queries.default_agent_id` is the scheduler's second resolution step, so
    it is a live dispatch path and gets the same check."""
    async with session_factory() as db:
        schedule, chosen, _ws = await _seed(db, next_run_at=_PAST)
        saved = await db.get(SavedQuery, schedule.saved_query_id)
        saved.default_agent_id = chosen.id
        chosen.access_mode = "restricted"
        await db.commit()

    await run_cycle(session_factory, now=_NOW)
    assert "no longer has access" in (await _fail_reason(session_factory))


async def test_auto_pick_skips_agents_the_owner_cannot_use(session_factory):
    """With no explicit choice the scheduler auto-picks — filtered by the owner, or
    omitting `agent_id` would route around a revoked grant."""
    async with session_factory() as db:
        _schedule, only_agent, _ws = await _seed(db, next_run_at=_PAST)
        only_agent.access_mode = "restricted"
        await db.commit()

    await run_cycle(session_factory, now=_NOW)
    assert (await _fail_reason(session_factory)) == "No accessible connected agent available"


async def test_a_grant_lets_the_auto_pick_find_the_agent(session_factory):
    async with session_factory() as db:
        schedule, only_agent, _ws = await _seed(db, next_run_at=_PAST)
        only_agent.access_mode = "restricted"
        db.add(AgentGrant(agent_id=only_agent.id, user_id=schedule.created_by, tier="use"))
        await db.commit()
        agent_id = only_agent.id

    await run_cycle(session_factory, now=_NOW)
    async with session_factory() as db:
        q = (await db.execute(select(Query).where(Query.origin == "scheduled"))).scalar_one()
        assert q.agent_id == agent_id
        assert q.status != "failed"


async def test_a_workspace_grant_covers_the_schedule_owner(session_factory):
    """The owner's access can come from the workspace the schedule lives in."""
    async with session_factory() as db:
        schedule, chosen, _ws = await _seed(db, next_run_at=_PAST)
        schedule.agent_id = chosen.id
        chosen.access_mode = "restricted"
        db.add(AgentGrant(agent_id=chosen.id, workspace_id=schedule.workspace_id, tier="use"))
        await db.commit()
        agent_id = chosen.id

    await run_cycle(session_factory, now=_NOW)
    async with session_factory() as db:
        q = (await db.execute(select(Query).where(Query.origin == "scheduled"))).scalar_one()
        assert q.agent_id == agent_id
        assert q.status != "failed"


async def test_a_deleted_configured_agent_fails_clearly(session_factory):
    async with session_factory() as db:
        schedule, chosen, _ws = await _seed(db, next_run_at=_PAST)
        schedule.agent_id = chosen.id
        await db.commit()
        registry.unregister(chosen.id)
        await db.delete(chosen)
        await db.commit()

    await run_cycle(session_factory, now=_NOW)
    assert (await _fail_reason(session_factory)) == "Configured agent no longer exists"


async def test_skip_if_previous_run_still_running(session_factory):
    async with session_factory() as db:
        schedule, agent, _ws = await _seed(db, next_run_at=_PAST)
        running = Query(
            workspace_id=schedule.workspace_id,
            agent_id=agent.id,
            sql="SELECT 42",
            status="running",
            origin="scheduled",
            schedule_id=schedule.id,
        )
        db.add(running)
        await db.flush()
        schedule.last_run_query_id = running.id
        await db.commit()
        running_id = running.id

    result = await run_cycle(session_factory, now=_NOW)
    assert result == {"status": "ran", "due": 1, "dispatched": 0, "skipped": 1}
    async with session_factory() as db:
        # No new run created; next_run_at still advanced (no backlog).
        runs = (await db.execute(select(Query).where(Query.origin == "scheduled"))).scalars().all()
        assert [r.id for r in runs] == [running_id]
        sched = await db.get(Schedule, schedule.id)
        assert _naive(sched.next_run_at) == _NEXT


async def test_offline_agent_records_failed_run(session_factory):
    async with session_factory() as db:
        # An explicit agent that is NOT connected -> fail fast.
        offline = uuid.uuid4()
        schedule, _agent, _ws = await _seed(
            db, connect_agent=False, next_run_at=_PAST, schedule_agent_id=offline
        )

    # A run row is still produced (and counts as dispatched), but it is recorded as
    # failed — the misconfigured agent surfaces in History rather than silently
    # re-picking another agent.
    result = await run_cycle(session_factory, now=_NOW)
    assert result["dispatched"] == 1
    async with session_factory() as db:
        q = (await db.execute(select(Query).where(Query.origin == "scheduled"))).scalar_one()
        assert q.status == "failed"
        assert q.error
        assert q.agent_id is None
        # The cycle still advanced the schedule rather than erroring out.
        sched = await db.get(Schedule, schedule.id)
        assert _naive(sched.next_run_at) == _NEXT


async def test_leadership_granted_on_non_postgres(session_factory):
    async with scheduler_leadership(session_factory) as is_leader:
        assert is_leader is True


async def test_run_tick_standby_skips_cycle(session_factory, monkeypatch):
    """A replica that loses leadership does not run a cycle (no double-dispatch)."""

    @contextlib.asynccontextmanager
    async def _no_leadership(_factory):
        yield False

    called = False

    async def _spy_run_cycle(*a, **k):
        nonlocal called
        called = True
        return {"status": "ran"}

    monkeypatch.setattr(scheduler_mod, "scheduler_leadership", _no_leadership)
    monkeypatch.setattr(scheduler_mod, "run_cycle", _spy_run_cycle)
    result = await run_tick(session_factory)
    assert result == {"status": "standby"}
    assert called is False


# --- restarting a terminated elastic agent -----------------------------------


@pytest.fixture
def elastic_enabled(monkeypatch):
    from api.config import settings
    from api.services.compute.backends import get_backend

    monkeypatch.setattr(settings, "elastic_compute_enabled", True)
    monkeypatch.setattr(settings, "elastic_provider", "null")
    backend = get_backend("null")
    backend._instances.clear()
    yield
    backend._instances.clear()


async def _seed_with_agent(db, *, provider, lifecycle, connected):
    """Seed a schedule pointing at an agent of a given kind and state."""
    suffix = uuid.uuid4().hex[:8]
    user = User(
        email=f"r-{suffix}@test.local", password_hash=hash_password("pw"), name="R", role="user"
    )
    db.add(user)
    await db.flush()
    ws, _cat = await seed_workspace(db, user_id=user.id, slug=f"rs-{suffix}", name="RS")
    agent = Agent(
        name=f"a-{suffix}",
        status="unavailable",
        capabilities={"extensions": ["httpfs", "iceberg"]},
        provider=provider,
        lifecycle=lifecycle,
        requested_cpu=2,
        requested_memory_gb=4,
    )
    db.add(agent)
    await db.flush()
    if connected:
        registry.register(agent.id, FakeWS())  # type: ignore[arg-type]
    saved = SavedQuery(workspace_id=ws.id, name="n", sql="SELECT 42", created_by=user.id)
    db.add(saved)
    await db.flush()
    schedule = Schedule(
        workspace_id=ws.id,
        job_type="saved_query",
        saved_query_id=saved.id,
        agent_id=agent.id,
        cron="0 2 * * *",
        enabled=True,
        next_run_at=_PAST,
        created_by=user.id,
    )
    db.add(schedule)
    await db.commit()
    return schedule, agent


async def _only_run(session_factory):
    async with session_factory() as db:
        return (await db.execute(select(Query).where(Query.origin == "scheduled"))).scalar_one()


async def test_terminated_elastic_agent_is_restarted_and_the_run_parks(
    session_factory, elastic_enabled
):
    """The reaper tears an idle elastic agent down between runs, so failing here
    would make it permanently unusable for unattended work."""
    async with session_factory() as db:
        _schedule, agent = await _seed_with_agent(
            db, provider="null", lifecycle="terminated", connected=False
        )
        agent_id = agent.id

    await run_cycle(session_factory, now=_NOW)

    run = await _only_run(session_factory)
    # Parked, not failed: no agent bound yet, and no error recorded.
    assert run.status == "queued"
    assert run.agent_id is None
    assert run.error is None

    async with session_factory() as db:
        after = await db.get(Agent, agent_id)
        assert after.lifecycle == "provisioning"
        assert after.terminated_at is None


async def test_offline_static_agent_still_fails(session_factory, elastic_enabled):
    """Nothing can start an operator-run host, so the run fails as it always did."""
    async with session_factory() as db:
        await _seed_with_agent(db, provider=None, lifecycle=None, connected=False)

    await run_cycle(session_factory, now=_NOW)

    run = await _only_run(session_factory)
    assert run.status == "failed"
    assert run.error == "Configured agent is not connected"


async def test_disconnected_but_running_elastic_agent_fails(session_factory, elastic_enabled):
    """`restart_elastic_agent` only acts on a torn-down instance, so a merely
    disconnected agent is not silently re-provisioned underneath itself."""
    async with session_factory() as db:
        await _seed_with_agent(db, provider="null", lifecycle="running", connected=False)

    await run_cycle(session_factory, now=_NOW)

    run = await _only_run(session_factory)
    assert run.status == "failed"
    assert run.error == "Configured agent is not connected"


async def test_restart_is_skipped_when_elastic_compute_is_disabled(session_factory):
    """No elastic_enabled fixture: restart returns None and the run fails cleanly
    rather than parking forever."""
    async with session_factory() as db:
        await _seed_with_agent(db, provider="null", lifecycle="terminated", connected=False)

    await run_cycle(session_factory, now=_NOW)

    run = await _only_run(session_factory)
    assert run.status == "failed"
    assert run.error == "Could not start the configured agent"


async def test_parked_run_dispatches_when_the_agent_registers(session_factory, elastic_enabled):
    """bind_scheduled_work is the other half: the restart parks the run, and the
    agent dialing home is what actually dispatches it."""
    from api.services.compute.service import bind_scheduled_work

    async with session_factory() as db:
        _schedule, agent = await _seed_with_agent(
            db, provider="null", lifecycle="terminated", connected=False
        )
        agent_id = agent.id

    await run_cycle(session_factory, now=_NOW)
    assert (await _only_run(session_factory)).agent_id is None

    # The agent comes up.
    async with session_factory() as db:
        fresh = await db.get(Agent, agent_id)
        registry.register(fresh.id, FakeWS())  # type: ignore[arg-type]
        bound = await bind_scheduled_work(db, fresh)

    assert bound == 1
    run = await _only_run(session_factory)
    assert run.agent_id == agent_id
    assert run.status != "failed"


async def test_binding_ignores_runs_for_a_different_agent(session_factory, elastic_enabled):
    """The binder matches the agent a schedule names, not merely 'some parked run'."""
    from api.services.compute.service import bind_scheduled_work

    async with session_factory() as db:
        await _seed_with_agent(db, provider="null", lifecycle="terminated", connected=False)
        other = Agent(name="unrelated", status="healthy", provider="null", lifecycle="running")
        db.add(other)
        await db.commit()
        other_id = other.id

    await run_cycle(session_factory, now=_NOW)

    async with session_factory() as db:
        bound = await bind_scheduled_work(db, await db.get(Agent, other_id))
    assert bound == 0
    assert (await _only_run(session_factory)).agent_id is None


async def test_a_parked_run_is_failed_if_the_agent_never_arrives(session_factory, elastic_enabled):
    """Otherwise the schedule's skip-if-running guard would block every later run
    behind a queued row that is never coming."""
    from api.config import settings
    from api.services.compute.reaper import _fail_stranded_queued

    async with session_factory() as db:
        await _seed_with_agent(db, provider="null", lifecycle="terminated", connected=False)

    await run_cycle(session_factory, now=_NOW)
    assert (await _only_run(session_factory)).status == "queued"

    # `started_at` is a DB default (real clock), not the fake `_NOW` the cycle ran
    # with, so the cutoff has to be measured from real time.
    later = datetime.now(tz=UTC) + timedelta(seconds=settings.elastic_provisioning_deadline_s + 60)
    async with session_factory() as db:
        failed = await _fail_stranded_queued(db, later)

    assert failed == 1
    run = await _only_run(session_factory)
    assert run.status == "failed"
    assert "No compute became available" in run.error


async def test_a_dispatched_scheduled_run_is_never_re_dispatched(session_factory, elastic_enabled):
    """Both a parked run and a dispatched-but-unacked one sit at `queued`; only
    the parked one has a NULL agent_id, which is what the binder keys on."""
    from api.services.compute.service import bind_scheduled_work

    async with session_factory() as db:
        _schedule, agent = await _seed_with_agent(
            db, provider="null", lifecycle="running", connected=True
        )
        agent_id = agent.id

    await run_cycle(session_factory, now=_NOW)
    run = await _only_run(session_factory)
    assert run.status == "queued" and run.agent_id == agent_id  # dispatched, awaiting ack

    async with session_factory() as db:
        bound = await bind_scheduled_work(db, await db.get(Agent, agent_id))
    assert bound == 0
