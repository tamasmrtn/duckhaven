"""The presence sweeper closes lifecycle runs whose agent went away silently."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker

from api.config import settings
from api.models.agent import Agent, AgentLifecycleEvent, AgentMetricsMinute
from api.models.query import Query
from api.models.workspace import Workspace
from api.services.agent_presence import (
    close_unfinished_run,
    fail_unanswerable_queries,
    run_tick,
    sweep_presence,
)
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


# ── Runs whose answer can no longer arrive ────────────────────────────────────


async def _present(db, *events) -> Agent:
    """An agent connected through some replica, with these lifecycle events."""
    agent = await _agent(db, last_ping_at=NOW, events=events)
    agent.owner_url = "http://replica-a"
    await db.commit()
    return agent


async def _run(db, agent, *, sent_s_ago, status="queued", **values) -> Query:
    workspace = Workspace(slug=f"ws-{uuid.uuid4().hex[:8]}", name="W")
    db.add(workspace)
    await db.flush()
    sent = NOW - timedelta(seconds=sent_s_ago)
    defaults = {"timeout_s": 600.0, "started_at": sent, "dispatched_at": sent}
    query = Query(
        workspace_id=workspace.id,
        agent_id=agent.id,
        sql="SELECT 1",
        status=status,
        **(defaults | values),
    )
    db.add(query)
    await db.commit()
    return query


async def _status(db, query: Query) -> tuple[str, str | None]:
    await db.refresh(query)
    return query.status, query.error


async def test_a_run_sent_before_its_agent_reconnected_is_failed(db_session):
    """It went out on the connection that closed, and the agent answers on that one only."""
    agent = await _present(
        db_session,
        ("connected", NOW - timedelta(hours=1)),
        ("disconnected", NOW - timedelta(seconds=40)),
        ("connected", NOW - timedelta(seconds=35)),
    )
    lost = await _run(db_session, agent, sent_s_ago=60, status="running", running_at=NOW)
    after = await _run(db_session, agent, sent_s_ago=10)

    assert await fail_unanswerable_queries(db_session, NOW) == 1

    status, error = await _status(db_session, lost)
    assert status == "failed"
    assert error == "The agent's connection closed before it sent this run's result. Run it again."
    assert lost.finished_at is not None
    # Sent on the new connection, which is still up.
    assert await _status(db_session, after) == ("queued", None)


async def test_a_run_sent_just_before_a_reconnect_is_given_the_benefit_of_the_doubt(db_session):
    """Within the margin the clocks cannot say which connection a run went out on."""
    agent = await _present(db_session, ("connected", NOW - timedelta(seconds=30)))
    run = await _run(db_session, agent, sent_s_ago=31)

    assert await fail_unanswerable_queries(db_session, NOW) == 0
    assert await _status(db_session, run) == ("queued", None)


async def test_a_run_whose_agent_is_gone_is_failed(db_session):
    """The sweeper dates a lost agent's disconnect at its last ping, which can be
    before the run was sent, so a missing agent is enough on its own."""
    agent = await _agent(
        db_session,
        last_ping_at=STALE,
        events=[("connected", STALE - timedelta(hours=1)), ("disconnected", STALE)],
    )
    run = await _run(db_session, agent, sent_s_ago=5, status="running", running_at=NOW)

    assert await fail_unanswerable_queries(db_session, NOW) == 1
    assert (await _status(db_session, run))[0] == "failed"


async def test_a_queued_run_on_a_connected_agent_waits_however_long(db_session):
    """The agent's admission queue has no time limit: a busy agent is not a lost one."""
    agent = await _present(db_session, ("connected", NOW - timedelta(hours=8)))
    run = await _run(db_session, agent, sent_s_ago=6 * 3600)

    assert await fail_unanswerable_queries(db_session, NOW) == 0
    assert await _status(db_session, run) == ("queued", None)


async def test_a_running_run_unanswered_past_its_timeout_is_failed(db_session):
    """For an answer lost on a connection that stayed up: the agent stops a run at its
    timeout and says so, so silence past it and a grace means the answer was lost."""
    agent = await _present(db_session, ("connected", NOW - timedelta(hours=3)))
    overdue = await _run(
        db_session, agent, sent_s_ago=800, status="running", running_at=NOW - timedelta(seconds=700)
    )
    in_grace = await _run(
        db_session, agent, sent_s_ago=700, status="running", running_at=NOW - timedelta(seconds=650)
    )
    export = await _run(
        db_session,
        agent,
        sent_s_ago=7200,
        status="running",
        running_at=NOW - timedelta(seconds=7000),
        timeout_s=12 * 3600.0,
        origin="export",
    )

    assert await fail_unanswerable_queries(db_session, NOW) == 1

    assert await _status(db_session, overdue) == (
        "failed",
        "The agent did not send this run's result within its timeout. Run it again.",
    )
    assert (await _status(db_session, in_grace))[0] == "running"
    # A long export is judged by its own timeout.
    assert (await _status(db_session, export))[0] == "running"


@pytest.mark.parametrize(
    "values",
    [
        {"origin": "session"},  # the SQL-session reaper's
        {"dispatched_at": None},  # sent before dispatch times were kept
    ],
)
async def test_runs_the_sweeper_does_not_judge_are_left_alone(db_session, values):
    agent = await _agent(db_session, last_ping_at=STALE)
    run = await _run(db_session, agent, sent_s_ago=3600, status="running", **values)

    assert await fail_unanswerable_queries(db_session, NOW) == 0
    assert (await _status(db_session, run))[0] == "running"


async def test_a_finished_run_is_never_touched(db_session):
    agent = await _agent(db_session, last_ping_at=STALE)
    run = await _run(db_session, agent, sent_s_ago=3600, status="done")

    assert await fail_unanswerable_queries(db_session, NOW) == 0
    assert (await _status(db_session, run))[0] == "done"


async def test_each_presence_tick_fails_lost_runs(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as db:
        agent = await _agent(db, last_ping_at=STALE)
        run = await _run(db, agent, sent_s_ago=600, status="running", running_at=NOW)

    await run_tick(factory)

    async with factory() as db:
        assert (await db.get(Query, run.id)).status == "failed"
