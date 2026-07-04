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
