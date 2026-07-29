from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa

from api.config import settings
from api.models.agent import Agent, AgentLifecycleEvent, AgentMetricsMinute
from api.services.agent_telemetry import (
    accumulate,
    flush_minute,
    purge_expired_metrics,
    record_lifecycle_event,
    record_lifecycle_event_now,
    reset_rollup_state,
    take_pending,
)


@pytest.fixture(autouse=True)
def _clean_rollup_state():
    """Accumulators are module-level and the test process is shared."""
    reset_rollup_state()
    yield
    reset_rollup_state()


@pytest.fixture
async def agent(db_session):
    a = Agent(name="telemetry-agent", status="healthy")
    db_session.add(a)
    await db_session.commit()
    await db_session.refresh(a)
    return a


def sample(at: str, *, cpu=10.0, mem=20.0, running=0, queued=0, sessions=0):
    return {
        "sampled_at": at,
        "cpu_percent": cpu,
        "memory_percent": mem,
        "running_queries": running,
        "queued_queries": queued,
        "session_count": sessions,
    }


# ── Lifecycle trail ──────────────────────────────────────────────────────────


async def test_record_lifecycle_event_rides_the_callers_commit(db_session, agent):
    record_lifecycle_event(db_session, agent.id, "connected")
    # Nothing is written until the caller commits — that is what keeps recording a
    # transition free of an extra round trip on the hot path.
    await db_session.commit()

    rows = (await db_session.execute(sa.select(AgentLifecycleEvent))).scalars().all()
    assert [(r.event, r.reason) for r in rows] == [("connected", None)]


async def test_record_lifecycle_event_now_commits_on_its_own(db_session, agent):
    await record_lifecycle_event_now(db_session, agent.id, "disconnected")
    rows = (await db_session.execute(sa.select(AgentLifecycleEvent))).scalars().all()
    assert [r.event for r in rows] == ["disconnected"]


async def test_record_lifecycle_event_now_never_raises(db_session):
    """Telemetry must not be why a socket handler tears down a connection."""
    await record_lifecycle_event_now(db_session, "not-a-uuid", "connected")  # type: ignore[arg-type]


# ── Minute accumulator ───────────────────────────────────────────────────────


def test_accumulate_returns_nothing_until_the_minute_rolls_over(agent):
    assert accumulate(agent.id, sample("2026-07-28T10:00:01+00:00")) is None
    assert accumulate(agent.id, sample("2026-07-28T10:00:59+00:00")) is None

    closed = accumulate(agent.id, sample("2026-07-28T10:01:00+00:00"))
    assert closed is not None
    assert closed.minute == datetime(2026, 7, 28, 10, 0, tzinfo=UTC)
    assert closed.count == 2


def test_accumulate_tracks_max_and_sum(agent):
    accumulate(agent.id, sample("2026-07-28T10:00:01+00:00", cpu=10, running=1, queued=0))
    accumulate(agent.id, sample("2026-07-28T10:00:31+00:00", cpu=50, running=3, queued=2))
    closed = accumulate(agent.id, sample("2026-07-28T10:01:01+00:00"))

    assert closed.cpu_max == 50
    assert closed.cpu_sum == 60
    assert closed.running_max == 3
    assert closed.queued_max == 2


def test_a_sample_from_a_backwards_clock_folds_into_the_open_minute(agent):
    """An agent whose clock steps back must not reopen a minute already closed."""
    accumulate(agent.id, sample("2026-07-28T10:05:00+00:00"))
    assert accumulate(agent.id, sample("2026-07-28T10:04:30+00:00")) is None


def test_take_pending_detaches_the_open_minute(agent):
    accumulate(agent.id, sample("2026-07-28T10:00:10+00:00"))
    pending = take_pending(agent.id)
    assert pending is not None and pending.count == 1
    assert take_pending(agent.id) is None


# ── Flush ────────────────────────────────────────────────────────────────────


async def test_flush_writes_one_row_per_minute(db_session, agent):
    accumulate(agent.id, sample("2026-07-28T10:00:01+00:00", cpu=10, mem=40, running=1))
    accumulate(agent.id, sample("2026-07-28T10:00:31+00:00", cpu=30, mem=60, running=3))
    closed = accumulate(agent.id, sample("2026-07-28T10:01:01+00:00"))

    await flush_minute(db_session, agent.id, closed)

    row = (await db_session.execute(sa.select(AgentMetricsMinute))).scalar_one()
    assert row.cpu_avg == 20.0
    assert row.cpu_max == 30.0
    assert row.mem_avg == 50.0
    assert row.running_max == 3
    assert row.sample_count == 2


async def test_flush_merges_when_a_second_replica_writes_the_same_minute(db_session, agent):
    """Ownership can move mid-minute; the second flush must combine, not clobber."""
    accumulate(agent.id, sample("2026-07-28T10:00:01+00:00", cpu=10, running=1))
    accumulate(agent.id, sample("2026-07-28T10:00:31+00:00", cpu=30, running=3))
    await flush_minute(db_session, agent.id, accumulate(agent.id, sample("2026-07-28T10:01:01Z")))

    reset_rollup_state()
    accumulate(agent.id, sample("2026-07-28T10:00:45+00:00", cpu=90, running=5))
    await flush_minute(db_session, agent.id, take_pending(agent.id))

    rows = (await db_session.execute(sa.select(AgentMetricsMinute))).scalars().all()
    assert len(rows) == 1, "the merge must not create a second row for the same minute"
    row = rows[0]
    assert row.sample_count == 3
    assert row.cpu_avg == pytest.approx((10 + 30 + 90) / 3)
    assert row.cpu_max == 90.0, "a max must survive the merge"
    assert row.running_max == 5


async def test_flush_of_an_empty_accumulator_writes_nothing(db_session, agent):
    accumulate(agent.id, sample("2026-07-28T10:00:01+00:00"))
    empty = take_pending(agent.id)
    empty.count = 0

    await flush_minute(db_session, agent.id, empty)
    assert (await db_session.execute(sa.select(AgentMetricsMinute))).first() is None


# ── Retention ────────────────────────────────────────────────────────────────


async def test_purge_deletes_only_rows_past_the_retention_window(db_session, agent, monkeypatch):
    monkeypatch.setattr(settings, "agent_metrics_retention_hours", 24.0)
    now = datetime.now(tz=UTC).replace(second=0, microsecond=0)
    for age_hours in (1, 12, 48):
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
    remaining = (await db_session.execute(sa.select(AgentMetricsMinute))).scalars().all()
    assert len(remaining) == 2


async def test_purge_runs_at_most_hourly(db_session, agent, monkeypatch):
    monkeypatch.setattr(settings, "agent_metrics_retention_hours", 24.0)
    assert await purge_expired_metrics(db_session) == 0

    # An expired row added *after* the first purge survives the second call: the
    # hourly guard is what keeps this cheap enough to hang off the per-minute flush.
    db_session.add(
        AgentMetricsMinute(
            agent_id=agent.id,
            minute=datetime.now(tz=UTC) - timedelta(hours=48),
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

    assert await purge_expired_metrics(db_session) == 0
    assert (await db_session.execute(sa.select(AgentMetricsMinute))).first() is not None
