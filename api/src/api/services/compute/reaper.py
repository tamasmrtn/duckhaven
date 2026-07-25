"""Scale-in + reconciliation for elastic agents.

A leader-elected background loop (Postgres advisory lock, mirroring the scheduler,
maintenance scanner, and SQL-session reaper) that:

* **terminates idle agents** — an elastic agent that has had no work for
  ``elastic_idle_timeout_s`` AND has no in-flight queries or open SQL sessions is
  torn down (so users pay nothing while nothing runs);
* **backstops lifetime** — an agent past ``elastic_max_lifetime_s`` is terminated
  once its work drains, bounding runaway long-lived instances;
* **fails stuck provisioning** — a row that never dialed home within
  ``elastic_provisioning_deadline_s`` is failed and its instance cleaned up;
* **reconciles leaks** — cloud instances with no live row are terminated, and rows
  whose instance has vanished are failed. Postgres is the state-of-record (I9);
  the cloud is reconciled to it.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.config import settings
from api.models.agent import Agent
from api.models.query import Query
from api.models.sql_session import SqlSession
from api.services.compute.backends import get_backend
from api.services.compute.service import terminate_agent

logger = logging.getLogger(__name__)

# Distinct advisory-lock key (cf. scheduler 0x64687371, scanner 0x64687363,
# sql-session reaper 0x64687373).
_REAPER_LOCK_KEY = 0x64687363 + 0x100

_ACTIVE_LIFECYCLE = ("provisioning", "running")
_QUERY_IN_FLIGHT = ("queued", "running")
_SESSION_OPEN = ("opening", "open", "closing")


def _aware(value: datetime) -> datetime:
    """Treat a naive timestamp (SQLite under tests) as UTC for comparison."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


@contextlib.asynccontextmanager
async def reaper_leadership(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[bool]:
    """Yield ``True`` iff this replica holds the cluster-wide elastic-reaper lock.
    On backends without advisory locks (SQLite under tests) leadership is granted."""
    async with session_factory() as db:
        if db.bind.dialect.name != "postgresql":
            yield True
            return
        got = bool(
            (
                await db.execute(
                    sa.text("SELECT pg_try_advisory_lock(:k)"), {"k": _REAPER_LOCK_KEY}
                )
            ).scalar()
        )
        try:
            yield got
        finally:
            if got:
                await db.execute(sa.text("SELECT pg_advisory_unlock(:k)"), {"k": _REAPER_LOCK_KEY})
                await db.commit()


async def _has_in_flight_work(db: AsyncSession, agent_id) -> bool:
    """Whether an agent has any in-flight query or open SQL session pinned to it.

    Guards termination: an idle *clock* is not enough — an agent running a long
    query, or holding an open BI/dbt session, must not be torn down under it.
    """
    q = (
        await db.execute(
            sa.select(sa.func.count())
            .select_from(Query)
            .where(Query.agent_id == agent_id, Query.status.in_(_QUERY_IN_FLIGHT))
        )
    ).scalar_one()
    if q:
        return True
    s = (
        await db.execute(
            sa.select(sa.func.count())
            .select_from(SqlSession)
            .where(SqlSession.agent_id == agent_id, SqlSession.status.in_(_SESSION_OPEN))
        )
    ).scalar_one()
    return bool(s)


async def _reap_lifecycle(db: AsyncSession, now: datetime) -> dict[str, int]:
    """Idle / max-lifetime / provisioning-deadline transitions."""
    lifetime_cutoff = now - timedelta(seconds=settings.elastic_max_lifetime_s)
    provisioning_cutoff = now - timedelta(seconds=settings.elastic_provisioning_deadline_s)
    reaped = {"idle": 0, "max_lifetime": 0, "provisioning_timeout": 0}

    agents = (
        (
            await db.execute(
                sa.select(Agent).where(
                    Agent.provider.is_not(None), Agent.lifecycle.in_(_ACTIVE_LIFECYCLE)
                )
            )
        )
        .scalars()
        .all()
    )

    for agent in agents:
        if agent.lifecycle == "provisioning":
            if _aware(agent.provisioned_at) < provisioning_cutoff:
                # Never dialed home. Fail the row and clean up any instance created.
                if agent.instance_id:
                    with contextlib.suppress(Exception):
                        await get_backend(agent.provider).terminate(agent.instance_id)
                agent.lifecycle = "failed"
                agent.status = "unavailable"
                agent.terminated_at = now
                await db.commit()
                reaped["provisioning_timeout"] += 1
                logger.info("Failed stuck-provisioning elastic agent %s", agent.id)
            continue

        # lifecycle == "running": never tear down over active work.
        if await _has_in_flight_work(db, agent.id):
            continue

        over_lifetime = _aware(agent.provisioned_at) < lifetime_cutoff
        last_active = agent.last_active_at or agent.provisioned_at
        # Each agent may carry its own idle timeout (chosen at create); fall back
        # to the global default when it doesn't.
        idle_timeout_s = agent.idle_timeout_s or settings.elastic_idle_timeout_s
        idle = _aware(last_active) < now - timedelta(seconds=idle_timeout_s)
        if over_lifetime:
            await terminate_agent(db, agent, reason="max_lifetime")
            reaped["max_lifetime"] += 1
        elif idle:
            await terminate_agent(db, agent, reason="idle")
            reaped["idle"] += 1

    return reaped


async def _reconcile_leaks(db: AsyncSession, provider: str) -> dict[str, int]:
    """Reconcile the cloud against Postgres for one provider.

    * A cloud instance backing no active row → terminate (orphan).
    * An active row whose instance is gone from the cloud → fail (dead instance).
    """
    result = {"orphans_terminated": 0, "dead_rows_failed": 0}
    backend = get_backend(provider)
    live = await backend.list_managed()

    rows = (
        (
            await db.execute(
                sa.select(Agent).where(
                    Agent.provider == provider, Agent.lifecycle.in_(_ACTIVE_LIFECYCLE)
                )
            )
        )
        .scalars()
        .all()
    )
    known = {a.instance_id for a in rows if a.instance_id}

    for instance_id in live - known:
        with contextlib.suppress(Exception):
            await backend.terminate(instance_id)
        result["orphans_terminated"] += 1
        logger.warning("Terminated orphan instance %s (provider %s)", instance_id, provider)

    now = datetime.now(tz=UTC)
    for agent in rows:
        if agent.instance_id and agent.instance_id not in live:
            agent.lifecycle = "failed"
            agent.status = "unavailable"
            agent.terminated_at = now
            result["dead_rows_failed"] += 1
            logger.warning("Failed agent %s: backing instance %s gone", agent.id, agent.instance_id)
    await db.commit()
    return result


async def run_cycle(session_factory: async_sessionmaker[AsyncSession]) -> dict[str, int]:
    now = datetime.now(tz=UTC)
    async with session_factory() as db:
        reaped = await _reap_lifecycle(db, now)
        reaped.update(await _reconcile_leaks(db, settings.elastic_provider))
    return reaped


async def run_tick(session_factory: async_sessionmaker[AsyncSession]) -> dict[str, int] | None:
    async with reaper_leadership(session_factory) as is_leader:
        if not is_leader:
            return None
        return await run_cycle(session_factory)


async def reaper_loop(session_factory: async_sessionmaker[AsyncSession]) -> None:
    """Background loop: reap idle/leaked elastic agents on a fixed tick. Each cycle
    is wrapped so one bad run never kills the loop; leadership is elected per tick."""
    logger.info(
        "Elastic compute reaper started (tick %.0fs, idle %.0fs, max %.0fs, provider %s)",
        settings.elastic_reaper_tick_s,
        settings.elastic_idle_timeout_s,
        settings.elastic_max_lifetime_s,
        settings.elastic_provider,
    )
    while True:
        try:
            result = await run_tick(session_factory)
            if result and any(result.values()):
                logger.info("Elastic reaper cycle: %s", result)
        except Exception as exc:  # noqa: BLE001 - the loop must survive any cycle failure
            logger.exception("Elastic reaper cycle failed: %s", exc)
        await asyncio.sleep(settings.elastic_reaper_tick_s)
