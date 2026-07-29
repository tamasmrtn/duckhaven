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
  the cloud is reconciled to it;
* **fails stranded pool runs** — a queued run that no agent will ever bind is failed
  rather than left waiting indefinitely.
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
from api.metrics import record_agents_reaped, set_reap_leader
from api.models.agent import Agent
from api.models.query import Query
from api.models.sql_session import SqlSession
from api.services.agent_telemetry import record_lifecycle_event
from api.services.compute.backends import get_backend
from api.services.compute.service import revoke_bootstrap_credentials, terminate_agent

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
                # It never registered, so its enrollment token is still live and
                # nothing else collects it -- and revoking it is what stops a slow
                # instance dialing home later and reviving this row.
                await revoke_bootstrap_credentials(db, agent.id)
                record_lifecycle_event(db, agent.id, "failed", reason="provisioning_timeout")
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


async def _fail_stranded_queued(db: AsyncSession, now: datetime) -> int:
    """Fail elastic pool runs that no agent is ever going to pick up.

    A run against the pool is parked ``queued`` with no agent and only leaves that state
    when a provisioned agent registers and binds it. Several things stop that happening:
    the per-pool cap was already reached by a row that never registers, provisioning
    failed outright, or the agent was failed at its deadline. None of them touch the
    parked run, so it stayed ``queued`` forever and the client polled an answer that was
    never coming.

    Bounding it here rather than at submission covers every one of those causes with one
    rule, and does not risk failing a run that supply was genuinely still arriving for.
    The budget is the provisioning deadline: once it passes, the agent that would have
    served this run has been failed too.
    """
    cutoff = now - timedelta(seconds=settings.elastic_provisioning_deadline_s)
    stranded = (
        (
            await db.execute(
                sa.select(Query).where(
                    Query.agent_id.is_(None),
                    Query.origin == "elastic",
                    Query.status == "queued",
                    Query.started_at < cutoff,
                )
            )
        )
        .scalars()
        .all()
    )
    for query in stranded:
        query.status = "failed"
        query.error = "No compute became available for this run."
        query.finished_at = now
        logger.warning("Failed stranded elastic query %s: no agent registered", query.id)
    if stranded:
        await db.commit()
    return len(stranded)


async def _reconcile_leaks(db: AsyncSession, provider: str) -> dict[str, int]:
    """Reconcile the cloud against Postgres for one provider.

    * A cloud instance backing no active row → terminate (orphan).
    * An active row whose instance is gone from the cloud → fail (dead instance).
    """
    result = {
        "orphans_terminated": 0,
        "dead_rows_failed": 0,
        "terminations_retried": 0,
        "terminations_completed": 0,
    }
    backend = get_backend(provider)
    live = await backend.list_managed()

    # Every row, not just the active ones. An instance whose row says it should be
    # gone is a *pending or failed deletion*, which is a different thing from an
    # instance nobody owns -- and on a backend whose delete is asynchronous it is
    # the normal state for tens of seconds after any routine scale-in. Selecting
    # only active rows made each of those land in `live - known`, so every idle
    # reap logged "Terminated orphan instance" and incremented the leak counter,
    # which is the signal an operator would watch for a genuine leak.
    rows = (await db.execute(sa.select(Agent).where(Agent.provider == provider))).scalars().all()
    active = {a.instance_id for a in rows if a.instance_id and a.lifecycle in _ACTIVE_LIFECYCLE}
    retired = {
        a.instance_id for a in rows if a.instance_id and a.lifecycle not in _ACTIVE_LIFECYCLE
    }

    for instance_id in live - active - retired:
        with contextlib.suppress(Exception):
            await backend.terminate(instance_id)
        result["orphans_terminated"] += 1
        logger.warning("Terminated orphan instance %s (provider %s)", instance_id, provider)

    # A row that has been retired while its instance is still there. Re-issuing the
    # delete is what makes terminate_agent's best-effort backend call eventually
    # stick, and re-deleting something already deleting is harmless. Quiet, because
    # this is the expected path rather than a fault.
    for instance_id in live & retired:
        with contextlib.suppress(Exception):
            await backend.terminate(instance_id)
        result["terminations_retried"] += 1
        logger.debug(
            "Re-issued delete for retired instance %s (provider %s)", instance_id, provider
        )

    now = datetime.now(tz=UTC)
    # A row is committed with its instance_id *before* the backend is asked to create
    # the instance, so that a crash between the two is always reconcilable. That leaves a
    # window in which an instance legitimately does not exist yet, and a cycle landing
    # inside it would fail a perfectly healthy agent that was still being created -- and
    # then terminate the instance as an orphan on the next pass, because its row is no
    # longer active.
    #
    # The exemption is deliberately narrow: only a row still *provisioning*, and only
    # inside the provisioning deadline. An agent that reached "running" has demonstrably
    # had an instance, so if it is missing now it really is gone, whatever its age; and
    # _reap_lifecycle fails a provisioning row once the deadline passes anyway.
    settle_cutoff = now - timedelta(seconds=settings.elastic_provisioning_deadline_s)
    for agent in rows:
        # Finish a termination that was interrupted. terminate_agent commits
        # "terminating", then calls the backend and closes the socket before
        # committing "terminated"; an interruption in that window stranded the row
        # permanently, because every reaper path filters on provisioning|running and
        # both admin routes reject it -- terminate wants provisioning|running,
        # restart wants terminated|failed. Once the instance is gone there is
        # nothing left to do but record it.
        if agent.lifecycle == "terminating" and agent.instance_id not in live:
            agent.lifecycle = "terminated"
            agent.status = "unavailable"
            agent.terminated_at = now
            record_lifecycle_event(db, agent.id, "terminated", reason="interrupted")
            result["terminations_completed"] += 1
            logger.info("Completed interrupted termination of agent %s", agent.id)
            continue
        if agent.lifecycle not in _ACTIVE_LIFECYCLE:
            continue
        if agent.instance_id and agent.instance_id not in live:
            if agent.lifecycle == "provisioning" and _aware(agent.provisioned_at) >= settle_cutoff:
                continue
            agent.lifecycle = "failed"
            agent.status = "unavailable"
            agent.terminated_at = now
            record_lifecycle_event(db, agent.id, "failed", reason="dead_row")
            result["dead_rows_failed"] += 1
            logger.warning("Failed agent %s: backing instance %s gone", agent.id, agent.instance_id)
    await db.commit()
    return result


async def _providers_to_reconcile(db: AsyncSession) -> list[str]:
    """Every provider this deployment may still hold instances for.

    Reconciling only the configured provider leaves instances stranded the moment an
    operator changes it -- flipping back to "null", or over to a second cloud, means the
    previous provider's orphans are never enumerated again, so they are never terminated
    and keep billing, and rows whose instance has gone are never failed. The set is
    therefore taken from the rows themselves, plus the configured provider so a cloud
    with instances but no rows left is still swept.
    """
    from_rows = (
        (
            await db.execute(
                sa.select(Agent.provider)
                .where(Agent.provider.is_not(None), Agent.lifecycle.in_(_ACTIVE_LIFECYCLE))
                .distinct()
            )
        )
        .scalars()
        .all()
    )
    return sorted({*from_rows, settings.elastic_provider})


async def run_cycle(session_factory: async_sessionmaker[AsyncSession]) -> dict[str, int]:
    now = datetime.now(tz=UTC)
    async with session_factory() as db:
        reaped = await _reap_lifecycle(db, now)
        totals = {
            "orphans_terminated": 0,
            "dead_rows_failed": 0,
            "terminations_retried": 0,
            "terminations_completed": 0,
        }
        for provider in await _providers_to_reconcile(db):
            try:
                result = await _reconcile_leaks(db, provider)
            except Exception:
                # One unreachable or unconfigured backend must not stop the others being
                # reconciled -- a provider left over from an earlier configuration is
                # exactly the case where its settings may no longer be present.
                logger.exception("Reconciliation failed for provider %s", provider)
                continue
            for key, value in result.items():
                totals[key] += value
        reaped.update(totals)
        reaped["stranded_queries_failed"] = await _fail_stranded_queued(db, now)
    return reaped


async def run_tick(session_factory: async_sessionmaker[AsyncSession]) -> dict[str, int] | None:
    async with reaper_leadership(session_factory) as is_leader:
        # Elastic agent counts are DB-wide; gate their gauge on the same election so
        # exactly one replica reports them (see api.metrics for the same rule
        # applied to the maintenance scanner).
        set_reap_leader(is_leader)
        if not is_leader:
            return None
        result = await run_cycle(session_factory)
        record_agents_reaped(result)
        return result


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
