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
from api.services.agent_monitoring import (
    ACTIVITY_QUERY,
    ACTIVITY_READY,
    build_grid,
    build_monitoring,
)
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
    """Range filtering and bucketing across a real timestamptz column."""
    grid = build_grid("1h")
    record_lifecycle_event(db_session, agent.id, "connected")
    await db_session.commit()

    for offset, running in ((2, 3), (5, 0)):
        db_session.add(
            AgentMetricsMinute(
                agent_id=agent.id,
                minute=grid.edges[offset],
                cpu_avg=40.0,
                cpu_max=75.0,
                mem_avg=10.0,
                mem_max=12.0,
                running_max=running,
                queued_max=0,
                session_max=0,
                sample_count=30,
            )
        )
    # A row just outside the window must not be picked up.
    db_session.add(
        AgentMetricsMinute(
            agent_id=agent.id,
            minute=grid.start - timedelta(minutes=5),
            cpu_avg=99.0,
            cpu_max=99.0,
            mem_avg=99.0,
            mem_max=99.0,
            running_max=99,
            queued_max=99,
            session_max=0,
            sample_count=30,
        )
    )
    await db_session.commit()

    data = await build_monitoring(db_session, agent, "1h")

    assert data["peak_query_count"][2]["running"] == 3
    assert data["utilization"][2]["cpu_max"] == 75.0
    assert max(p["running"] for p in data["peak_query_count"]) == 3, "out-of-window row leaked in"
    assert data["activity"][2]["state"] == ACTIVITY_QUERY
    assert data["activity"][5]["state"] == ACTIVITY_READY


async def test_query_charts_read_through_the_new_index(db_session, agent, workspace):
    """Exercises the (agent_id, finished_at) range scan the charts depend on."""
    grid = build_grid("1h")
    for i in range(5):
        db_session.add(
            Query(
                workspace_id=workspace.id,
                agent_id=agent.id,
                sql="select 1",
                status="failed" if i == 0 else "done",
                error="queue full" if i == 0 else None,
                started_at=grid.edges[1],
                finished_at=grid.edges[1] + timedelta(seconds=1),
            )
        )
    await db_session.commit()

    data = await build_monitoring(db_session, agent, "1h")
    assert data["summary"]["completed"] == 5
    assert data["summary"]["failed"] == 1
    assert [(f["reason"], f["count"]) for f in data["failures"]] == [("queue_full", 1)]
