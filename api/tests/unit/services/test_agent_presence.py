"""The presence sweeper closes lifecycle runs whose agent went away silently."""

from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa

from api.config import settings
from api.models.agent import Agent, AgentLifecycleEvent, AgentMetricsMinute
from api.services.agent_presence import close_unfinished_run, sweep_presence
from api.services.agent_registry import registry
from api.services.agent_telemetry import record_lifecycle_event


@pytest.fixture(autouse=True)
def _clean_registry():
    registry._connections.clear()
    yield
    registry._connections.clear()


NOW = datetime.now(tz=UTC).replace(microsecond=0)
STALE = NOW - timedelta(
    seconds=settings.agent_presence_ttl_s + settings.agent_presence_sweep_interval_s + 60
)


async def _agent(db, *, last_ping_at=None, events=()) -> Agent:
    agent = Agent(name="a", status="healthy", last_ping_at=last_ping_at)
    db.add(agent)
    await db.commit()
    for event, at in events:
        record_lifecycle_event(db, agent.id, event, at=at)
    await db.commit()
    return agent


async def _events(db, agent_id) -> list[tuple[str, str | None, datetime]]:
    rows = (
        (
            await db.execute(
                sa.select(AgentLifecycleEvent)
                .where(AgentLifecycleEvent.agent_id == agent_id)
                .order_by(AgentLifecycleEvent.at)
            )
        )
        .scalars()
        .all()
    )
    return [(r.event, r.reason, r.at.replace(tzinfo=UTC)) for r in rows]


async def test_stale_connected_agent_is_closed_at_its_last_ping(db_session):
    agent = await _agent(
        db_session, last_ping_at=STALE, events=[("connected", STALE - timedelta(hours=1))]
    )

    assert await sweep_presence(db_session, NOW) == 1

    # Dated at the last proof of life, not at the sweep: the outage starts there.
    assert (await _events(db_session, agent.id))[-1] == ("disconnected", "presence_lost", STALE)


async def test_sweep_is_idempotent(db_session):
    await _agent(db_session, last_ping_at=STALE, events=[("connected", STALE)])
    assert await sweep_presence(db_session, NOW) == 1
    assert await sweep_presence(db_session, NOW) == 0


async def test_fresh_presence_is_left_alone(db_session):
    fresh = NOW - timedelta(seconds=30)
    agent = await _agent(db_session, last_ping_at=fresh, events=[("connected", fresh)])
    assert await sweep_presence(db_session, NOW) == 0
    assert [e for e, _, _ in await _events(db_session, agent.id)] == ["connected"]


async def test_socket_held_by_this_replica_is_never_swept(db_session):
    """The local registry is authoritative even when the DB watermark lags."""
    agent = await _agent(db_session, last_ping_at=STALE, events=[("connected", STALE)])
    registry.register(agent.id, object())  # type: ignore[arg-type]
    assert await sweep_presence(db_session, NOW) == 0


async def test_missing_watermark_falls_back_to_the_connect_time(db_session):
    agent = await _agent(db_session, last_ping_at=None, events=[("connected", STALE)])
    assert await sweep_presence(db_session, NOW) == 1
    assert (await _events(db_session, agent.id))[-1][2] == STALE


async def test_already_disconnected_agent_is_untouched(db_session):
    agent = await _agent(
        db_session,
        last_ping_at=STALE,
        events=[("connected", STALE - timedelta(minutes=5)), ("disconnected", STALE)],
    )
    assert await sweep_presence(db_session, NOW) == 0
    assert len(await _events(db_session, agent.id)) == 2


async def test_reconnect_closes_a_run_left_open_at_its_last_reported_minute(db_session):
    """A replica that crashed before the sweep ran leaves 'connected' as the latest
    event. The next connect (which has already refreshed the watermark) closes that
    run at the end of the last minute it reported."""
    started = NOW - timedelta(hours=2)
    agent = await _agent(db_session, last_ping_at=NOW, events=[("connected", started)])
    last_minute = NOW - timedelta(hours=1)
    db_session.add(
        AgentMetricsMinute(
            agent_id=agent.id,
            minute=last_minute,
            cpu_avg=1,
            cpu_max=1,
            mem_avg=1,
            mem_max=1,
            running_max=0,
            queued_max=0,
            session_max=0,
            sample_count=30,
        )
    )
    await db_session.commit()

    assert await close_unfinished_run(db_session, agent.id) is True
    await db_session.commit()
    assert (await _events(db_session, agent.id))[-1] == (
        "disconnected",
        "presence_lost",
        last_minute + timedelta(minutes=1),
    )
    # Once closed there is nothing left to repair.
    assert await close_unfinished_run(db_session, agent.id) is False


async def test_reconnect_closes_a_silent_run_just_after_it_opened(db_session):
    started = NOW - timedelta(hours=2)
    agent = await _agent(db_session, last_ping_at=NOW, events=[("connected", started)])
    assert await close_unfinished_run(db_session, agent.id) is True
    await db_session.commit()
    assert [e for e, _, _ in await _events(db_session, agent.id)] == ["connected", "disconnected"]
