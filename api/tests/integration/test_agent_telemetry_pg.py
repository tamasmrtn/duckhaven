"""Agent telemetry against real Postgres.

The unit suite for this feature runs entirely on SQLite, and the two pieces most
likely to diverge between the dialects are exactly what it adds: a merge-on-conflict
write (spelled as UPDATE-then-INSERT with a portable ``CASE``, because Postgres wants
``GREATEST`` where SQLite wants ``max``) and timestamp-range aggregation across a
``timestamptz`` column. Both are covered here against the real thing.

Also covers the advisory-lock path in the retention purge, which the unit suite skips
entirely — SQLite has no advisory locks, so that branch is never taken there.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select

from api.config import settings
from api.models.agent import Agent, AgentMetricsMinute
from api.models.query import Query
from api.models.workspace import Workspace
from api.services.agent_monitoring import build_monitoring
from api.services.agent_telemetry import (
    accumulate,
    flush_minute,
    purge_expired_metrics,
    record_lifecycle_event,
    reset_rollup_state,
    take_pending,
)

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _clean_rollup_state():
    reset_rollup_state()
    yield
    reset_rollup_state()


@pytest.fixture
async def agent(db_session):
    a = Agent(name="pg-telemetry-agent", status="healthy", provider="null", lifecycle="running")
    db_session.add(a)
    await db_session.commit()
    await db_session.refresh(a)
    return a


@pytest.fixture
async def workspace(db_session):
    """A bare workspace row.

    Deliberately not ``workspace_factory``: that one provisions a real Polaris
    catalog, which would make these tests skip whenever Polaris is down. Nothing
    here touches a catalog — the queries never run, they are only rows to aggregate.
    """
    ws = Workspace(slug=f"pg-telemetry-{uuid4().hex[:8]}", name="PG Telemetry")
    db_session.add(ws)
    await db_session.commit()
    await db_session.refresh(ws)
    return ws


def _sample(at: str, *, cpu=10.0, running=0, sessions=0):
    return {
        "sampled_at": at,
        "cpu_percent": cpu,
        "memory_percent": 20.0,
        "running_queries": running,
        "queued_queries": 0,
        "session_count": sessions,
    }


async def test_rollup_merge_on_real_postgres(db_session, agent):
    """The CASE-based max and weighted mean must behave the same as on SQLite.

    Postgres has no two-argument ``max``; if the merge ever regressed to
    ``sa.func.max`` this would fail here and pass in the unit suite.
    """
    accumulate(agent.id, _sample("2026-07-28T10:00:01+00:00", cpu=10, running=1))
    accumulate(agent.id, _sample("2026-07-28T10:00:31+00:00", cpu=30, running=3))
    await flush_minute(db_session, agent.id, accumulate(agent.id, _sample("2026-07-28T10:01:01Z")))

    reset_rollup_state()
    accumulate(agent.id, _sample("2026-07-28T10:00:45+00:00", cpu=90, running=5))
    await flush_minute(db_session, agent.id, take_pending(agent.id))

    rows = (
        (
            await db_session.execute(
                select(AgentMetricsMinute).where(AgentMetricsMinute.agent_id == agent.id)
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert rows[0].sample_count == 3
    assert rows[0].cpu_avg == pytest.approx((10 + 30 + 90) / 3)
    assert rows[0].cpu_max == 90.0
    assert rows[0].running_max == 5


async def test_purge_takes_and_releases_the_advisory_lock(db_session, agent, monkeypatch):
    """The lock branch is dead code under SQLite; exercise it for real here."""
    monkeypatch.setattr(settings, "agent_metrics_retention_hours", 24.0)
    now = datetime.now(tz=UTC).replace(second=0, microsecond=0)
    for age_hours in (2, 72):
        db_session.add(
            AgentMetricsMinute(
                agent_id=agent.id,
                minute=now - timedelta(hours=age_hours),
                cpu_avg=1,
                cpu_max=1,
                mem_avg=1,
                mem_max=1,
                running_max=0,
                queued_max=0,
                session_max=0,
                sample_count=1,
            )
        )
    await db_session.commit()

    assert await purge_expired_metrics(db_session) == 1

    # Releasing matters as much as taking: a lock left held would block every
    # later purge on every replica for the life of the connection.
    reset_rollup_state()
    assert await purge_expired_metrics(db_session) == 0


async def test_monitoring_aggregation_over_timestamptz(db_session, agent):
    """Range filtering and bucketing of the rollup across a real timestamptz column."""
    now = datetime.now(tz=UTC)
    record_lifecycle_event(db_session, agent.id, "connected", at=now - timedelta(hours=2))
    await db_session.commit()
    minute = (now - timedelta(minutes=10)).replace(second=0, microsecond=0)
    for offset, cpu_max in ((0, 75.0), (3, 20.0)):
        db_session.add(
            AgentMetricsMinute(
                agent_id=agent.id,
                minute=minute + timedelta(minutes=offset),
                cpu_avg=40.0,
                cpu_max=cpu_max,
                mem_avg=10.0,
                mem_max=12.0,
                running_max=0,
                queued_max=0,
                session_max=0,
                sample_count=30,
            )
        )
    # A row just outside the range must not be picked up.
    db_session.add(
        AgentMetricsMinute(
            agent_id=agent.id,
            minute=now - timedelta(hours=2),
            cpu_avg=99.0,
            cpu_max=99.0,
            mem_avg=99.0,
            mem_max=99.0,
            running_max=0,
            queued_max=0,
            session_max=0,
            sample_count=30,
        )
    )
    await db_session.commit()

    data = await build_monitoring(db_session, agent, window="1h", now=now)

    by_t = {b["t"]: b for b in data["buckets"]}
    assert by_t[minute]["cpu_max"] == 75.0
    assert data["summary"]["cpu_peak"] == 75.0, "out-of-range row leaked in"
    assert data["summary"]["uptime_s"] == 3600


async def test_query_activity_over_timestamptz(db_session, agent, workspace):
    """The overlap predicate and the parked-run branch of the UNION ALL on Postgres,
    including a query that started before the range and one still running."""
    now = datetime.now(tz=UTC)
    record_lifecycle_event(db_session, agent.id, "connected", at=now - timedelta(hours=3))
    for i in range(5):
        db_session.add(
            Query(
                workspace_id=workspace.id,
                agent_id=agent.id,
                sql="select 1",
                status="failed" if i == 0 else "done",
                error="queue full" if i == 0 else None,
                started_at=now - timedelta(minutes=30),
                running_at=now - timedelta(minutes=30),
                finished_at=now - timedelta(minutes=29),
            )
        )
    db_session.add(
        Query(
            workspace_id=workspace.id,
            agent_id=agent.id,
            sql="select 1",
            status="done",
            started_at=now - timedelta(minutes=70),
            running_at=now - timedelta(minutes=70),
            finished_at=now - timedelta(minutes=50),
        )
    )
    db_session.add(
        Query(
            workspace_id=workspace.id,
            agent_id=agent.id,
            sql="select 1",
            status="running",
            started_at=now - timedelta(minutes=2),
            running_at=now - timedelta(minutes=2),
        )
    )
    await db_session.commit()

    data = await build_monitoring(db_session, agent, window="1h", now=now)
    summary = data["summary"]
    assert summary["finished"] == 6  # including the long run, which finished inside it
    assert summary["failed_by_reason"] == {"queue_full": 1}
    assert summary["peak_running"] == 5
    # 10 min of the long run inside the range + 1 min of the five + 2 min in flight.
    assert summary["busy_s"] == 13 * 60


async def test_presence_sweep_and_lifecycle_purge_on_real_postgres(
    db_session, pg_engine, agent, monkeypatch
):
    """The sweeper's leadership lock is exclusive and released, its backdated close
    lands on a timestamptz column, and the lifecycle purge's correlated DELETE keeps
    the seed event."""
    import random

    from sqlalchemy.ext.asyncio import async_sessionmaker

    from api.models.agent import AgentLifecycleEvent
    from api.services import agent_presence

    monkeypatch.setattr(agent_presence, "_PRESENCE_LOCK_KEY", random.randint(1, 2**31 - 1))
    monkeypatch.setattr(settings, "agent_metrics_retention_hours", 24.0)
    factory = async_sessionmaker(pg_engine, expire_on_commit=False)

    async with agent_presence.presence_leadership(factory) as first:
        async with agent_presence.presence_leadership(factory) as second:
            assert first is True and second is False
    async with agent_presence.presence_leadership(factory) as again:
        assert again is True

    now = datetime.now(tz=UTC)
    stale = now - timedelta(hours=1)
    agent.last_ping_at = stale
    record_lifecycle_event(db_session, agent.id, "connected", at=now - timedelta(hours=72))
    record_lifecycle_event(db_session, agent.id, "disconnected", at=now - timedelta(hours=60))
    record_lifecycle_event(db_session, agent.id, "connected", at=now - timedelta(hours=2))
    await db_session.commit()

    assert await agent_presence.sweep_presence(db_session, now) == 1
    await purge_expired_metrics(db_session)

    rows = (
        (await db_session.execute(select(AgentLifecycleEvent).order_by(AgentLifecycleEvent.at)))
        .scalars()
        .all()
    )
    assert [(r.event, r.reason) for r in rows] == [
        ("disconnected", None),
        ("connected", None),
        ("disconnected", "presence_lost"),
    ]
    assert rows[-1].at == stale


async def test_split_minute_merges_coverage_and_oom_kills_on_real_postgres(db_session, agent):
    """The NULL-aware merge (coalesce + value) for the columns added in 0050."""
    minute = "2026-07-28T10:00:"
    for second, oom in (("02", None), ("30", 1), ("40", 2)):
        payload = {
            "sampled_at": f"{minute}{second}+00:00",
            "cpu_percent": 1.0,
            "memory_percent": 1.0,
            "interval_s": 2.0,
        }
        if oom is not None:
            payload["oom_kills"] = oom
        accumulate(agent.id, payload)
        await flush_minute(db_session, agent.id, take_pending(agent.id))

    row = (await db_session.execute(select(AgentMetricsMinute))).scalar_one()
    assert row.covered_s == 6.0
    assert row.oom_kills == 3
