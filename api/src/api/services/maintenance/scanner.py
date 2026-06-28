"""The maintenance scanner: a periodic driver that asks agents to probe tables.

It enumerates tables via Polaris, picks the stale ones (prioritized, budgeted),
and dispatches health probes through the existing query-dispatch fabric. It does
*not* wait for results — probes stream back as QUERY_DONE frames and are recorded
by ``ingest.record_health_sample``. This keeps the cycle cheap and scalable to
thousands of tables: a bounded number of probes per cycle, fair round-robin
coverage via a persisted cursor, and graceful skips when no agent is connected.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.config import settings
from api.models.catalog import Catalog, WorkspaceCatalog
from api.models.maintenance import MaintenancePolicy, TableHealthSample
from api.models.query import Query
from api.models.workspace import Workspace
from api.services.maintenance.policy import get_or_create_policy
from api.services.polaris import PolarisClient, PolarisError
from api.services.query import dispatch_query, pick_agent_for

logger = logging.getLogger(__name__)

# Cluster-wide advisory-lock key electing the single scanner leader each tick.
# Arbitrary constant ('dhsc'); only its uniqueness against other advisory locks
# in this database matters.
_SCANNER_LOCK_KEY = 0x64687363

_FREQUENCY_WINDOW = {
    "hourly": timedelta(hours=1),
    "daily": timedelta(days=1),
}


def _due(policy: MaintenancePolicy, now: datetime) -> bool:
    if not policy.scan_enabled or policy.scan_frequency == "off":
        return False
    window = _FREQUENCY_WINDOW.get(policy.scan_frequency)
    if window is None or policy.last_scan_at is None:
        return True
    last = policy.last_scan_at
    if last.tzinfo is None:
        last = last.replace(tzinfo=UTC)
    return now - last >= window


async def run_cycle(
    session_factory: async_sessionmaker[AsyncSession],
    polaris: PolarisClient,
    *,
    force: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Run one scan cycle. ``force`` bypasses the cadence check (manual trigger)."""
    now = now or datetime.now(tz=UTC)
    async with session_factory() as db:
        policy = await get_or_create_policy(db)
        if not force and not _due(policy, now):
            return {"status": "skipped", "reason": "not_due"}

        include_orphans = _deep_scan_due(policy, now)
        target_file_bytes = int(policy.thresholds.get("target_file_bytes", 128 * 1024**2))
        window = _FREQUENCY_WINDOW.get(policy.scan_frequency, timedelta(days=1))

        candidates = await _candidate_tables(db, polaris)
        stale = await _filter_stale(db, candidates, now - window)
        ordered = _rotate(stale, policy.scan_cursor)
        budget = ordered[: policy.max_tables_per_cycle]

        dispatched = 0
        for catalog, ws, schema, table in budget:
            agent = await pick_agent_for(db, ws)
            if agent is None:
                # No compatible agent connected; dispatch is fail-fast, so stop
                # this cycle and try again next tick rather than erroring.
                logger.info("Maintenance scan: no agent for catalog %s; skipping", catalog.slug)
                break
            await _dispatch_probe(
                db, catalog, ws, agent.id, schema, table, target_file_bytes, include_orphans
            )
            dispatched += 1

        if budget:
            policy.scan_cursor = _cursor_key(budget[-1])
        policy.last_scan_at = now
        if include_orphans:
            policy.last_deep_scan_at = now
        await db.commit()
        return {
            "status": "ran",
            "dispatched": dispatched,
            "candidates": len(candidates),
            "stale": len(stale),
            "deep": include_orphans,
        }


def _deep_scan_due(policy: MaintenancePolicy, now: datetime) -> bool:
    last = policy.last_deep_scan_at
    if last is None:
        return True
    if last.tzinfo is None:
        last = last.replace(tzinfo=UTC)
    return (now - last).total_seconds() >= settings.maintenance_deep_scan_interval_s


async def _candidate_tables(
    db: AsyncSession, polaris: PolarisClient
) -> list[tuple[Catalog, Workspace, str, str]]:
    """Every (catalog, workspace, schema, table) currently exposed.

    Catalogs are the unit of scanning; a catalog shared across workspaces is
    enumerated once, with one representative workspace carried along to provide
    dispatch context (agent selection runs over the workspace's bound catalogs).
    """
    rows = (
        await db.execute(
            sa.select(Catalog, Workspace)
            .join(WorkspaceCatalog, WorkspaceCatalog.catalog_id == Catalog.id)
            .join(Workspace, Workspace.id == WorkspaceCatalog.workspace_id)
            .order_by(Catalog.slug)
        )
    ).all()
    representative: dict[Any, tuple[Catalog, Workspace]] = {}
    for catalog, ws in rows:
        representative.setdefault(catalog.id, (catalog, ws))

    out: list[tuple[Catalog, Workspace, str, str]] = []
    for catalog, ws in representative.values():
        try:
            schemas = await polaris.list_schemas(catalog.polaris_name)
            for schema in schemas:
                tables = await polaris.list_tables(catalog.polaris_name, schema.name)
                out.extend((catalog, ws, schema.name, t.name) for t in tables)
        except PolarisError as exc:
            logger.warning("Maintenance scan: enumerate failed for %s: %s", catalog.slug, exc)
    return out


async def _filter_stale(
    db: AsyncSession,
    candidates: list[tuple[Catalog, Workspace, str, str]],
    cutoff: datetime,
) -> list[tuple[Catalog, Workspace, str, str]]:
    """Drop tables already sampled since ``cutoff`` (prioritize the stale ones)."""
    recent = {
        (row[0], row[1], row[2])
        for row in (
            await db.execute(
                sa.select(
                    TableHealthSample.catalog_id,
                    TableHealthSample.schema_name,
                    TableHealthSample.table_name,
                ).where(TableHealthSample.scanned_at >= cutoff)
            )
        ).all()
    }
    return [c for c in candidates if (c[0].id, c[2], c[3]) not in recent]


def _cursor_key(candidate: tuple[Catalog, Workspace, str, str]) -> str:
    catalog, _ws, schema, table = candidate
    return f"{catalog.id}|{schema}|{table}"


def _rotate(
    candidates: list[tuple[Catalog, Workspace, str, str]], cursor: str | None
) -> list[tuple[Catalog, Workspace, str, str]]:
    """Round-robin: start just after the last table scanned last cycle."""
    ordered = sorted(candidates, key=_cursor_key)
    if not cursor:
        return ordered
    after = [c for c in ordered if _cursor_key(c) > cursor]
    before = [c for c in ordered if _cursor_key(c) <= cursor]
    return after + before


async def _dispatch_probe(
    db: AsyncSession,
    catalog: Catalog,
    workspace: Workspace,
    agent_id: Any,
    schema: str,
    table: str,
    target_file_bytes: int,
    include_orphans: bool,
) -> None:
    query = Query(
        workspace_id=workspace.id,
        agent_id=agent_id,
        user_id=None,
        sql="SELECT 1",
        status="queued",
        origin="maintenance",
    )
    db.add(query)
    await db.flush()
    await dispatch_query(
        db,
        query,
        timeout_s=120.0,
        active_catalog=catalog.slug,
        health_for={
            "catalog": catalog.slug,
            "schema": schema,
            "table": table,
            "target_file_bytes": target_file_bytes,
            "include_orphans": include_orphans,
        },
    )


@contextlib.asynccontextmanager
async def scan_leadership(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[bool]:
    """Yield ``True`` iff this replica holds the cluster-wide scanner lock.

    Uses a Postgres session-level advisory lock so that, with multiple API
    replicas all running the scanner, exactly one runs a cycle per tick. The lock
    is held on this session's connection for the duration of the ``with`` block
    (covering the cycle that runs on its own connections) and released after. On
    backends without advisory locks (SQLite in unit tests) leadership is always
    granted, preserving single-process behavior.
    """
    async with session_factory() as db:
        if db.bind.dialect.name != "postgresql":
            yield True
            return
        got = bool(
            (
                await db.execute(
                    sa.text("SELECT pg_try_advisory_lock(:k)"), {"k": _SCANNER_LOCK_KEY}
                )
            ).scalar()
        )
        try:
            yield got
        finally:
            if got:
                await db.execute(sa.text("SELECT pg_advisory_unlock(:k)"), {"k": _SCANNER_LOCK_KEY})
                await db.commit()


async def run_tick(
    session_factory: async_sessionmaker[AsyncSession],
    polaris: PolarisClient,
) -> dict[str, Any]:
    """One scheduler tick: run a cycle only if this replica wins leadership."""
    async with scan_leadership(session_factory) as is_leader:
        if not is_leader:
            return {"status": "standby"}
        return await run_cycle(session_factory, polaris)


async def scanner_loop(
    session_factory: async_sessionmaker[AsyncSession],
    polaris: PolarisClient,
) -> None:
    """Background loop: wake on a fixed tick and run a cycle when one is due.

    Mirrors the agent's retention sweep loop. Each cycle is wrapped so one bad
    run never kills the loop. Leadership is elected per tick so it is safe to run
    this loop on every replica.
    """
    logger.info("Maintenance scanner started (tick %.0fs)", settings.maintenance_scan_tick_s)
    while True:
        try:
            result = await run_tick(session_factory, polaris)
            if result.get("status") == "ran":
                logger.info("Maintenance scan: %s", result)
        except Exception as exc:  # noqa: BLE001 - the loop must survive any cycle failure
            logger.exception("Maintenance scan cycle failed: %s", exc)
        await asyncio.sleep(settings.maintenance_scan_tick_s)
