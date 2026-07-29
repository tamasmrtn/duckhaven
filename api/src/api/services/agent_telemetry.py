"""Durable agent telemetry: the lifecycle trail and the per-minute metrics rollup.

Nothing an agent reported about itself used to survive. Utilization lived in a
150-sample in-memory ring buffer (``services.agent_registry``) — about five minutes,
per replica, gone on restart — and the ``agents`` row is mutated in place, so a
restart erased the previous run. The per-agent monitoring page needs 1–24h of both,
which is what this module writes.

It deliberately adds no new observation points. Every write here hangs off a
transition the control plane already noticed and already reported to Prometheus or
to a span; this is a third sink for the same events, differing only in being
queryable by the product itself. Where the reaper already counts an outcome, the
counter's name *is* the ``reason`` recorded, so the page and Grafana cannot tell
different stories about why an agent went away.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api.config import settings
from api.models.agent import AgentLifecycleEvent, AgentMetricsMinute

logger = logging.getLogger(__name__)

# Advisory-lock key for the retention purge, in the same "dhs" family as the
# reaper (0x64687363), scheduler (0x64687371) and SQL-session reaper (0x64687373).
_PURGE_LOCK_KEY = 0x64687374


def record_lifecycle_event(
    db: AsyncSession,
    agent_id: uuid.UUID,
    event: str,
    *,
    reason: str | None = None,
) -> None:
    """Append one lifecycle transition for ``agent_id``. The caller commits.

    Synchronous because it only stages a row — it rides along on whichever commit
    the caller was already going to make, so recording a transition never adds a
    round trip to a scale-out or a socket handshake.
    """
    db.add(AgentLifecycleEvent(agent_id=agent_id, event=event, reason=reason))


async def record_lifecycle_event_now(
    db: AsyncSession,
    agent_id: uuid.UUID,
    event: str,
    *,
    reason: str | None = None,
) -> None:
    """Record a transition and commit it, swallowing any failure.

    For call sites with no commit of their own to ride on: the WebSocket handler
    opens a short-lived session per frame, and a disconnect runs during teardown.
    Telemetry must never be why a socket handler raises — a lost event costs a gap
    in a chart, an exception here would cost the connection.
    """
    try:
        record_lifecycle_event(db, agent_id, event, reason=reason)
        await db.commit()
    except Exception:
        logger.exception("Could not record %s lifecycle event for agent %s", event, agent_id)


# ── Per-minute metrics rollup ────────────────────────────────────────────────


@dataclass
class MinuteAccumulator:
    """One agent's samples for one minute, still in memory.

    Only the replica holding an agent's socket receives its samples, so exactly one
    accumulator exists per agent cluster-wide — the same ownership rule that keeps
    the Prometheus per-agent gauges from double-counting.
    """

    minute: datetime
    cpu_sum: float = 0.0
    cpu_max: float = 0.0
    mem_sum: float = 0.0
    mem_max: float = 0.0
    running_max: int = 0
    queued_max: int = 0
    session_max: int = 0
    count: int = 0

    def add(self, sample: dict) -> None:
        cpu = float(sample.get("cpu_percent") or 0.0)
        mem = float(sample.get("memory_percent") or 0.0)
        self.cpu_sum += cpu
        self.mem_sum += mem
        self.cpu_max = max(self.cpu_max, cpu)
        self.mem_max = max(self.mem_max, mem)
        self.running_max = max(self.running_max, int(sample.get("running_queries") or 0))
        self.queued_max = max(self.queued_max, int(sample.get("queued_queries") or 0))
        self.session_max = max(self.session_max, int(sample.get("session_count") or 0))
        self.count += 1


@dataclass
class _RollupState:
    pending: dict[str, MinuteAccumulator] = field(default_factory=dict)
    last_purge: datetime | None = None


_state = _RollupState()


def _minute_of(sample: dict) -> datetime:
    """The minute a sample belongs to, from the agent's own clock when it gave one.

    Agents stamp ``sampled_at``; falling back to the control plane's clock only
    matters for an agent too old to send it, where a sub-second skew is irrelevant
    at minute resolution.
    """
    raw = sample.get("sampled_at")
    if isinstance(raw, str):
        try:
            parsed = datetime.fromisoformat(raw)
            at = parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
        except ValueError:
            at = datetime.now(tz=UTC)
    else:
        at = datetime.now(tz=UTC)
    return at.replace(second=0, microsecond=0)


def accumulate(agent_id: uuid.UUID, sample: dict) -> MinuteAccumulator | None:
    """Fold one sample in; return the previous minute's accumulator once it closes.

    Returning the closed minute rather than writing it keeps this function
    synchronous and free of I/O, so the ~2s sample path stays a pure in-memory
    update and only the once-a-minute rollover costs a database round trip.
    """
    key = str(agent_id)
    minute = _minute_of(sample)
    current = _state.pending.get(key)
    closed: MinuteAccumulator | None = None
    if current is None or current.minute != minute:
        # A sample for an *older* minute arrives only if the agent's clock went
        # backwards; fold it into the open minute rather than reopening a closed one.
        if current is not None and minute < current.minute:
            current.add(sample)
            return None
        closed = current
        current = MinuteAccumulator(minute=minute)
        _state.pending[key] = current
    current.add(sample)
    return closed


def take_pending(agent_id: uuid.UUID) -> MinuteAccumulator | None:
    """Detach an agent's still-open minute, for flushing at disconnect.

    Without this the final partial minute before a disconnect is never written,
    which is exactly the minute an operator looks at after an agent goes away.
    """
    return _state.pending.pop(str(agent_id), None)


async def flush_minute(db: AsyncSession, agent_id: uuid.UUID, acc: MinuteAccumulator) -> None:
    """Persist one closed minute, merging with anything already recorded for it.

    Written as UPDATE-then-INSERT rather than a dialect upsert because merging needs
    a two-argument max, and the two dialects disagree on how to spell one (Postgres
    ``GREATEST``, SQLite ``max``) — ``CASE`` is the portable form, and the unit suite
    runs on SQLite. The merge matters only during an ownership handoff, when two
    replicas can each hold part of the same minute; the loser of the INSERT race
    retries the UPDATE and combines instead of overwriting.
    """
    table = AgentMetricsMinute.__table__
    where = sa.and_(table.c.agent_id == agent_id, table.c.minute == acc.minute)

    def keep_max(col: sa.Column, value: float) -> sa.Case:
        """The larger of the stored value and ours, spelled portably."""
        return sa.case((col > value, col), else_=value)

    def _merge_update() -> sa.Update:
        total = table.c.sample_count + acc.count
        return (
            sa.update(table)
            .where(where)
            .values(
                cpu_avg=(table.c.cpu_avg * table.c.sample_count + acc.cpu_sum) / total,
                mem_avg=(table.c.mem_avg * table.c.sample_count + acc.mem_sum) / total,
                cpu_max=keep_max(table.c.cpu_max, acc.cpu_max),
                mem_max=keep_max(table.c.mem_max, acc.mem_max),
                running_max=keep_max(table.c.running_max, acc.running_max),
                queued_max=keep_max(table.c.queued_max, acc.queued_max),
                session_max=keep_max(table.c.session_max, acc.session_max),
                sample_count=total,
            )
        )

    if acc.count == 0:
        return
    try:
        updated = await db.execute(_merge_update())
        if updated.rowcount == 0:
            await db.execute(
                sa.insert(table).values(
                    agent_id=agent_id,
                    minute=acc.minute,
                    cpu_avg=acc.cpu_sum / acc.count,
                    cpu_max=acc.cpu_max,
                    mem_avg=acc.mem_sum / acc.count,
                    mem_max=acc.mem_max,
                    running_max=acc.running_max,
                    queued_max=acc.queued_max,
                    session_max=acc.session_max,
                    sample_count=acc.count,
                )
            )
        await db.commit()
    except IntegrityError:
        # Another replica inserted this minute between our UPDATE and our INSERT.
        await db.rollback()
        await db.execute(_merge_update())
        await db.commit()
    except Exception:
        logger.exception("Could not flush metrics rollup for agent %s", agent_id)
        await db.rollback()


async def purge_expired_metrics(db: AsyncSession) -> int:
    """Delete rollup rows past the retention window. Runs at most hourly.

    Piggybacks on the flush path instead of taking a loop of its own: the flush
    already happens once a minute per connected agent, and an advisory lock keeps
    concurrent replicas from all issuing the same DELETE. On a dialect without
    advisory locks (SQLite, under tests) the in-process hourly guard is enough,
    because there is only one process.
    """
    now = datetime.now(tz=UTC)
    if _state.last_purge is not None and (now - _state.last_purge) < timedelta(hours=1):
        return 0
    _state.last_purge = now

    cutoff = now - timedelta(hours=settings.agent_metrics_retention_hours)
    if db.bind.dialect.name == "postgresql":
        got = (
            await db.execute(sa.text("SELECT pg_try_advisory_lock(:k)"), {"k": _PURGE_LOCK_KEY})
        ).scalar()
        if not got:
            return 0
    try:
        deleted = await db.execute(
            sa.delete(AgentMetricsMinute).where(AgentMetricsMinute.minute < cutoff)
        )
        await db.commit()
    finally:
        if db.bind.dialect.name == "postgresql":
            await db.execute(sa.text("SELECT pg_advisory_unlock(:k)"), {"k": _PURGE_LOCK_KEY})
            await db.commit()
    if deleted.rowcount:
        logger.info("Purged %d expired agent metric rows", deleted.rowcount)
    return deleted.rowcount


def reset_rollup_state() -> None:
    """Drop all in-flight accumulators. For tests, which share a process."""
    _state.pending.clear()
    _state.last_purge = None
