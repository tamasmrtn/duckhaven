"""Close the lifecycle trail for agents that went away without saying so.

The trail records ``disconnected`` from the WebSocket handler's ``finally``. That
never runs when the replica holding the socket crashes, and an agent that loses
its network looks connected until someone notices. The monitoring timeline replays
the trail, so a missing ``disconnected`` read as an agent that stayed up and idle
for days after it died.

This loop is the someone. An agent whose latest event is ``connected`` but whose
presence watermark (``last_ping_at``) has gone stale gets a ``disconnected`` with
reason ``presence_lost``, dated at that watermark: the last moment anything proved
it was alive, which is the most honest end for the span.

It also fails runs whose answer can no longer arrive. An agent answers a run on
the connection the run arrived on and never resends, so a run sent before that
connection closed (a network drop, an API replica restarting) stayed queued or
running for good, and kept an elastic agent from ever counting as idle.

It runs on every deployment, not only elastic ones, and so it also owns the hourly
retention purge, which used to hang off the per-minute flush and therefore never
ran while no agent was connected.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.config import settings
from api.models.agent import Agent, AgentLifecycleEvent, AgentMetricsMinute
from api.models.query import Query
from api.services.agent_registry import registry
from api.services.agent_telemetry import (
    latest_events,
    purge_expired_metrics,
    record_lifecycle_event,
)

logger = logging.getLogger(__name__)

# Same "dhs" advisory-lock family as the reaper (0x64687363), scheduler
# (0x64687371), SQL-session reaper (0x64687373) and retention purge (0x64687374).
_PRESENCE_LOCK_KEY = 0x64687375


def _aware(value: datetime) -> datetime:
    """Treat a naive timestamp (SQLite under tests) as UTC."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


@contextlib.asynccontextmanager
async def presence_leadership(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[bool]:
    """Yield ``True`` iff this replica holds the cluster-wide presence lock. On
    backends without advisory locks (SQLite under tests) leadership is granted."""
    async with session_factory() as db:
        if db.bind.dialect.name != "postgresql":
            yield True
            return
        got = bool(
            (
                await db.execute(
                    sa.text("SELECT pg_try_advisory_lock(:k)"), {"k": _PRESENCE_LOCK_KEY}
                )
            ).scalar()
        )
        try:
            yield got
        finally:
            if got:
                await db.execute(
                    sa.text("SELECT pg_advisory_unlock(:k)"), {"k": _PRESENCE_LOCK_KEY}
                )
                await db.commit()


async def sweep_presence(db: AsyncSession, now: datetime | None = None) -> int:
    """Record ``disconnected`` for every agent whose presence has lapsed.

    Idempotent: once an agent's latest event is ``disconnected`` it is left alone,
    and because the event is dated at the stale watermark, a reconnect racing the
    sweep still sorts after it. Returns how many agents were closed out.
    """
    now = now or datetime.now(tz=UTC)
    # The TTL is what the rest of the control plane treats as "gone"; one sweep
    # interval on top covers a live socket whose 30 s presence refresh landed just
    # after this sweep read the row.
    stale_before = now - timedelta(
        seconds=settings.agent_presence_ttl_s + settings.agent_presence_sweep_interval_s
    )
    local = registry.connected_ids()
    latest = await latest_events(db)
    candidates = [
        agent_id
        for agent_id, event in latest.items()
        if event.event == "connected" and str(agent_id) not in local
    ]
    if not candidates:
        return 0

    pings = dict(
        (
            await db.execute(
                sa.select(Agent.id, Agent.last_ping_at).where(Agent.id.in_(candidates))
            )
        ).all()
    )
    closed = 0
    for agent_id in candidates:
        # An agent that never refreshed its watermark was last seen when it connected.
        last_seen = _aware(pings.get(agent_id) or latest[agent_id].at)
        if last_seen >= stale_before:
            continue
        record_lifecycle_event(db, agent_id, "disconnected", reason="presence_lost", at=last_seen)
        closed += 1
    if closed:
        await db.commit()
        logger.info("Closed the lifecycle trail of %d agent(s) whose presence lapsed", closed)
    return closed


#: How much later than a run's dispatch a connection event must be to prove the run
#: went out on the connection that closed. The dispatch is stamped by the API just
#: before the send, the event by the database, so the two clocks differ a little.
DISPATCH_MARGIN = timedelta(seconds=2)
#: Time an agent has, past a run's own timeout, to report it: writing and sending
#: the result. The agent stops a run at its timeout and reports that too.
RESULT_GRACE = timedelta(seconds=60)


async def fail_unanswerable_queries(db: AsyncSession, now: datetime | None = None) -> int:
    """Fail sent runs whose agent can no longer answer them. Returns how many.

    A run counts as lost when its agent is not connected, or has connected or
    disconnected since the run was sent: either way the connection it arrived on
    is gone. A running run still unanswered past its timeout and
    :data:`RESULT_GRACE` is failed too, for an answer lost on a connection that
    stayed up. A queued run on a connected agent is left alone however old: the
    agent's admission queue has no time limit. Session statements have their own
    reaper, and runs sent before ``dispatched_at`` was kept are not judged.
    """
    now = now or datetime.now(tz=UTC)
    sent = (
        (
            await db.execute(
                sa.select(Query).where(
                    Query.status.in_(("queued", "running")),
                    Query.agent_id.is_not(None),
                    Query.dispatched_at.is_not(None),
                    Query.dispatched_at < now - DISPATCH_MARGIN,
                    sa.or_(Query.origin.is_(None), Query.origin != "session"),
                )
            )
        )
        .scalars()
        .all()
    )
    if not sent:
        return 0
    agents = list({q.agent_id for q in sent})
    changed = dict(
        (
            await db.execute(
                sa.select(AgentLifecycleEvent.agent_id, sa.func.max(AgentLifecycleEvent.at))
                .where(
                    AgentLifecycleEvent.agent_id.in_(agents),
                    AgentLifecycleEvent.event.in_(("connected", "disconnected")),
                )
                .group_by(AgentLifecycleEvent.agent_id)
            )
        ).all()
    )
    # Connected anywhere in the cluster: as dispatch_query decides it, in one read.
    present_after = now - timedelta(seconds=settings.agent_presence_ttl_s)
    connected = {uuid.UUID(a) for a in registry.connected_ids()} | set(
        (
            await db.execute(
                sa.select(Agent.id).where(
                    Agent.id.in_(agents),
                    Agent.owner_url.is_not(None),
                    Agent.last_ping_at >= present_after,
                )
            )
        ).scalars()
    )
    failed = 0
    for query in sent:
        dispatched = _aware(query.dispatched_at)
        last_change = changed.get(query.agent_id)
        if query.agent_id not in connected or (
            last_change is not None and _aware(last_change) > dispatched + DISPATCH_MARGIN
        ):
            error = "The agent's connection closed before it sent this run's result. Run it again."
        elif (
            query.status == "running"
            and query.running_at is not None
            and query.timeout_s is not None
            and _aware(query.running_at) + timedelta(seconds=query.timeout_s) + RESULT_GRACE < now
        ):
            error = "The agent did not send this run's result within its timeout. Run it again."
        else:
            continue
        query.status, query.error, query.finished_at = "failed", error, now
        logger.warning("Failed query %s on agent %s: %s", query.id, query.agent_id, error)
        failed += 1
    if failed:
        await db.commit()
    return failed


async def close_unfinished_run(db: AsyncSession, agent_id: uuid.UUID) -> bool:
    """On a new connect, close a previous run that was never closed.

    A run is left open when its replica died before the sweeper got to it. Called
    right after the new socket claims ownership, which has already overwritten the
    presence watermark, so the old run's last proof of life is its newest persisted
    metrics minute (one-minute resolution), or its own start when it never reported.
    The gap between the two runs then reads as the outage it was. Stages the event
    for the caller's commit and returns whether it did.
    """
    latest = (await latest_events(db, [agent_id])).get(agent_id)
    if latest is None or latest.event != "connected":
        return False
    started = _aware(latest.at)
    last_minute = (
        await db.execute(
            sa.select(sa.func.max(AgentMetricsMinute.minute)).where(
                AgentMetricsMinute.agent_id == agent_id,
                AgentMetricsMinute.minute >= started,
            )
        )
    ).scalar_one_or_none()
    # A run that never reported closes a millisecond after it opened, so the pair
    # still sorts connected-then-disconnected when replayed by time.
    last_seen = (
        _aware(last_minute) + timedelta(minutes=1)
        if last_minute
        else started + timedelta(milliseconds=1)
    )
    record_lifecycle_event(
        db,
        agent_id,
        "disconnected",
        reason="presence_lost",
        at=min(last_seen, datetime.now(tz=UTC)),
    )
    return True


async def run_tick(session_factory: async_sessionmaker[AsyncSession]) -> int | None:
    """One leader-gated sweep plus the (hourly-guarded) retention purge."""
    async with presence_leadership(session_factory) as leader:
        if not leader:
            return None
        async with session_factory() as db:
            closed = await sweep_presence(db)
            await fail_unanswerable_queries(db)
            await purge_expired_metrics(db)
            return closed


async def presence_loop(session_factory: async_sessionmaker[AsyncSession]) -> None:
    """Background loop: sweep on a fixed tick. A failed cycle never kills the loop."""
    logger.info(
        "Agent presence sweeper started (tick %.0fs, presence TTL %.0fs)",
        settings.agent_presence_sweep_interval_s,
        settings.agent_presence_ttl_s,
    )
    while True:
        try:
            await run_tick(session_factory)
        except Exception as exc:  # noqa: BLE001 - the loop must survive any cycle failure
            logger.exception("Agent presence sweep failed: %s", exc)
        await asyncio.sleep(settings.agent_presence_sweep_interval_s)
