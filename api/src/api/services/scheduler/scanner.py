"""The job scheduler: a periodic, leader-elected driver that runs schedules.

Each tick it finds due ``Schedule`` rows and dispatches each through the existing
query-dispatch fabric — it is a *new caller* of that path, not a second execution
engine. It does not wait for results: runs stream back as ``QUERY_DONE`` frames and
are recorded on the ``queries`` row by the agent websocket handler.

Generic by design: ``Schedule.job_type`` discriminates the work and
``_dispatch_schedule`` is the seam that routes each type to its handler. v1
implements only ``"saved_query"``. Coordination mirrors the maintenance scanner: a
Postgres advisory lock elects one leader per tick, so it is safe to run on every
replica.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.config import settings
from api.models.agent import Agent
from api.models.query import Query, SavedQuery, Schedule
from api.models.workspace import Workspace
from api.services.agent_access import tier_at_least, tier_for_principal
from api.services.agent_dispatch import is_agent_connected
from api.services.compute import service as compute_service
from api.services.query import dispatch_query, pick_agent_for
from api.services.scheduler.cron import next_run

logger = logging.getLogger(__name__)

# Cluster-wide advisory-lock key electing the single scheduler leader each tick.
# Arbitrary constant ('dhsq'); only its uniqueness against other advisory locks in
# this database matters (distinct from the maintenance scanner's 0x64687363).
_SCHEDULER_LOCK_KEY = 0x64687371

_TERMINAL = {"done", "failed", "cancelled"}


async def run_cycle(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Run one scheduler cycle: dispatch every schedule whose next run is due."""
    now = now or datetime.now(tz=UTC)
    async with session_factory() as db:
        due = (
            (
                await db.execute(
                    sa.select(Schedule).where(
                        Schedule.enabled.is_(True),
                        Schedule.next_run_at.isnot(None),
                        Schedule.next_run_at <= now,
                    )
                )
            )
            .scalars()
            .all()
        )
        dispatched = 0
        skipped = 0
        for schedule in due:
            if await _dispatch_schedule(db, schedule, now):
                dispatched += 1
            else:
                skipped += 1
        await db.commit()
        return {"status": "ran", "due": len(due), "dispatched": dispatched, "skipped": skipped}


async def _dispatch_schedule(db: AsyncSession, schedule: Schedule, now: datetime) -> bool:
    """Dispatch one schedule. Returns True if a run was started, False if skipped.

    Always advances ``next_run_at`` so a slow or skipped tick never causes a
    backlog — there is no backfill.
    """
    advance = next_run(schedule.cron, now)

    # Skip-if-running: never start a new run while the previous one is in flight.
    if schedule.last_run_query_id is not None:
        prev = await db.get(Query, schedule.last_run_query_id)
        if prev is not None and prev.status not in _TERMINAL:
            schedule.next_run_at = advance
            logger.info("Scheduled run skipped (previous still running): schedule=%s", schedule.id)
            return False

    if schedule.job_type == "saved_query":
        query = await _run_saved_query(db, schedule, now)
    else:
        logger.warning(
            "Unknown schedule job_type %r; skipping schedule=%s", schedule.job_type, schedule.id
        )
        schedule.next_run_at = advance
        return False

    if query is None:
        schedule.next_run_at = advance
        return False

    schedule.last_run_at = now
    schedule.last_run_query_id = query.id
    schedule.next_run_at = advance
    logger.info(
        "Scheduled run: schedule=%s query=%s agent=%s status=%s",
        schedule.id,
        query.id,
        query.agent_id,
        query.status,
    )
    return True


@dataclass(frozen=True)
class _Resolution:
    """What `_resolve_agent` decided for one run.

    Exactly one of the three is meaningful: an ``agent`` to dispatch to now,
    ``starting`` when an elastic agent is being provisioned and the run should park,
    or an ``error`` to record the run as failed with.
    """

    agent: Agent | None = None
    error: str | None = None
    starting: bool = False


async def _run_saved_query(db: AsyncSession, schedule: Schedule, now: datetime) -> Query | None:
    """Run the schedule's saved query verbatim, recorded as origin="scheduled".

    Returns the created ``Query`` (so the caller can stamp the schedule), or None
    if the target saved query no longer exists.
    """
    saved = await db.get(SavedQuery, schedule.saved_query_id) if schedule.saved_query_id else None
    if saved is None:
        logger.warning("Schedule %s has no saved query; skipping", schedule.id)
        return None
    workspace = await db.get(Workspace, schedule.workspace_id)

    resolved = await _resolve_agent(db, schedule, saved, workspace)
    agent = resolved.agent

    query = Query(
        workspace_id=schedule.workspace_id,
        agent_id=agent.id if agent is not None else None,
        user_id=None,
        sql=saved.sql,
        status="queued",
        origin="scheduled",
        schedule_id=schedule.id,
    )
    db.add(query)
    await db.flush()

    if resolved.starting:
        # The agent is being provisioned. Leave the run parked `queued` with no
        # agent; `bind_scheduled_work` dispatches it when the agent dials home,
        # and the reaper fails it if that never happens. Same shape as an elastic
        # pool run parked during a cold start.
        logger.info("Schedule %s parked pending agent start: query=%s", schedule.id, query.id)
    elif agent is None:
        query.status = "failed"
        query.error = resolved.error
        query.finished_at = now
    else:
        try:
            # Scheduled runs have no caller (user_id=None for audit); grants are
            # evaluated against the saved query's creator.
            await dispatch_query(db, query, principal_id=saved.created_by)
        except ValueError as exc:
            # Fail fast like a manual dispatch: the run is recorded as failed and
            # surfaces in History / the run list. One bad schedule never blocks
            # the rest of the cycle.
            query.status = "failed"
            query.error = str(exc)
            query.finished_at = now

    saved.last_run_at = now
    return query


async def _resolve_agent(
    db: AsyncSession, schedule: Schedule, saved: SavedQuery, workspace: Workspace | None
) -> _Resolution:
    """Resolve the agent for a scheduled run.

    Order: the schedule's explicit ``agent_id`` → the saved query's
    ``default_agent_id`` → auto-pick a compatible connected agent.

    An explicit choice that is offline is *not* silently re-picked. What happens
    instead depends on what kind of agent it is:

    - A **static** agent that is offline fails the run. Nothing here can start it —
      it is an operator-run host, and the control plane only ever accepts its
      inbound socket.
    - An **elastic** agent that is ``terminated`` or ``failed`` is re-provisioned,
      and the run parks ``queued`` until it dials home. Failing instead would make
      an idle-terminated agent permanently unusable for unattended work: the reaper
      tears it down between runs precisely because nothing is using it, so every
      subsequent run would fail on the consequence of the previous one succeeding.
    - An elastic agent that is merely disconnected while still ``running`` is not
      restartable and fails like a static one; ``restart_elastic_agent`` only acts
      on a torn-down instance.

    Whichever branch wins, the agent must still be usable by ``schedule.created_by``
    — the person who chose it. Access is re-evaluated here, on every fire, rather
    than snapshotted when the schedule was created: an ACL you cannot revoke is not
    an ACL, and someone who leaves the team would otherwise keep running work on the
    agent forever. This is the same shape as the catalog-grant check
    ``_run_saved_query`` already makes against the saved query's creator.

    A revoked schedule is *not* disabled and *not* silently re-routed to some other
    agent the owner can use — either would reinterpret an operator's explicit
    compute choice. Its runs fail with the reason below, which surfaces in History
    and the runs feed, and re-granting resumes it with no further action.
    """
    owner_id = schedule.created_by
    for chosen in (schedule.agent_id, saved.default_agent_id):
        if chosen is None:
            continue
        agent = await db.get(Agent, chosen)
        if agent is None:
            return _Resolution(error="Configured agent no longer exists")
        if not tier_at_least(await tier_for_principal(db, owner_id, agent), "use"):
            return _Resolution(error="Schedule owner no longer has access to the configured agent")
        if await is_agent_connected(db, chosen):
            return _Resolution(agent=agent)
        if agent.provider is not None and agent.lifecycle in ("terminated", "failed"):
            started = await compute_service.restart_elastic_agent(db, agent)
            if started is None:
                return _Resolution(error="Could not start the configured agent")
            logger.info("Schedule %s starting terminated agent %s", schedule.id, agent.id)
            return _Resolution(starting=True)
        return _Resolution(error="Configured agent is not connected")

    if workspace is None:
        return _Resolution(error="Workspace missing for schedule")
    # Scoped to the owner, or auto-select would route around a revoked grant.
    agent = await pick_agent_for(db, workspace, principal_id=owner_id)
    if agent is None:
        return _Resolution(error="No accessible connected agent available")
    return _Resolution(agent=agent)


@contextlib.asynccontextmanager
async def scheduler_leadership(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[bool]:
    """Yield ``True`` iff this replica holds the cluster-wide scheduler lock.

    Mirrors the maintenance scanner's ``scan_leadership``: a Postgres session-level
    advisory lock ensures exactly one replica runs a cycle per tick. On backends
    without advisory locks (SQLite under tests) leadership is always granted.
    """
    async with session_factory() as db:
        if db.bind.dialect.name != "postgresql":
            yield True
            return
        got = bool(
            (
                await db.execute(
                    sa.text("SELECT pg_try_advisory_lock(:k)"), {"k": _SCHEDULER_LOCK_KEY}
                )
            ).scalar()
        )
        try:
            yield got
        finally:
            if got:
                await db.execute(
                    sa.text("SELECT pg_advisory_unlock(:k)"), {"k": _SCHEDULER_LOCK_KEY}
                )
                await db.commit()


async def run_tick(session_factory: async_sessionmaker[AsyncSession]) -> dict[str, Any]:
    """One scheduler tick: run a cycle only if this replica wins leadership."""
    async with scheduler_leadership(session_factory) as is_leader:
        if not is_leader:
            return {"status": "standby"}
        return await run_cycle(session_factory)


async def scheduler_loop(session_factory: async_sessionmaker[AsyncSession]) -> None:
    """Background loop: wake on a fixed tick and dispatch due schedules.

    Each cycle is wrapped so one bad run never kills the loop. Leadership is elected
    per tick, so it is safe to run this loop on every replica.
    """
    logger.info("Scheduler started (tick %.0fs)", settings.scheduler_tick_s)
    while True:
        try:
            result = await run_tick(session_factory)
            if result.get("status") == "ran" and result.get("dispatched"):
                logger.info("Scheduler cycle: %s", result)
        except Exception as exc:  # noqa: BLE001 - the loop must survive any cycle failure
            logger.exception("Scheduler cycle failed: %s", exc)
        await asyncio.sleep(settings.scheduler_tick_s)
