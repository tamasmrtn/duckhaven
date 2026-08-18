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
from api.metrics import set_scan_leader
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

# Safety net for change-based skipping: a table whose snapshot id never changes
# is still re-probed at least this often, so a silently-broken table doesn't go
# unobserved forever.
_MAX_SAMPLE_AGE = timedelta(days=7)

# Health samples older than this are pruned each cycle; the growth trend only
# reads the recent window, so older rows accumulate forever otherwise.
_SAMPLE_RETENTION_DAYS = 90

# TTL for the Polaris table-enumeration cache (see _enumerate_catalog).
_ENUMERATION_TTL = timedelta(hours=1)
_enumeration_cache: dict[str, tuple[datetime, list[tuple[str, str]]]] = {}


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

        await _prune_old_samples(db, now)
        include_orphans = _deep_scan_due(policy, now)
        target_file_bytes = int(policy.thresholds.get("target_file_bytes", 128 * 1024**2))

        candidates = await _candidate_tables(db, polaris)
        stale = await _filter_changed(db, polaris, candidates, _MAX_SAMPLE_AGE)
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


async def _prune_old_samples(db: AsyncSession, now: datetime) -> None:
    """Delete health samples older than ``_SAMPLE_RETENTION_DAYS``.

    Runs once per scan cycle (the scanner already runs periodically, so no
    separate loop or lock). The growth trend only reads the recent window, so
    older rows are dead weight.
    """
    cutoff = now - timedelta(days=_SAMPLE_RETENTION_DAYS)
    await db.execute(sa.delete(TableHealthSample).where(TableHealthSample.scanned_at < cutoff))


async def _enumerate_catalog(polaris: PolarisClient, catalog: Catalog) -> list[tuple[str, str]]:
    """(schema, table) pairs for one catalog, cached for ``_ENUMERATION_TTL``.

    Enumeration is a per-catalog ``list_schemas`` + ``list_tables`` round-trip;
    caching it keeps the scan cycle cheap on large deployments. New tables appear
    within the TTL; a Polaris error invalidates the entry so a transient failure
    doesn't pin a stale list.
    """
    now = datetime.now(tz=UTC)
    cached = _enumeration_cache.get(catalog.slug)
    if cached is not None and now - cached[0] < _ENUMERATION_TTL:
        return cached[1]
    try:
        out: list[tuple[str, str]] = []
        schemas = await polaris.list_schemas(catalog.polaris_name)
        for schema in schemas:
            tables = await polaris.list_tables(catalog.polaris_name, schema.name)
            out.extend((schema.name, t.name) for t in tables)
    except PolarisError as exc:
        _enumeration_cache.pop(catalog.slug, None)
        logger.warning("Maintenance scan: enumerate failed for %s: %s", catalog.slug, exc)
        return []
    _enumeration_cache[catalog.slug] = (now, out)
    return out


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
        for schema, table in await _enumerate_catalog(polaris, catalog):
            out.append((catalog, ws, schema, table))
    return out


async def _latest_snapshot_ids(
    db: AsyncSession,
) -> dict[tuple[Any, str, str], tuple[int | None, datetime]]:
    """Latest sample's (snapshot_id, scanned_at) per (catalog, schema, table)."""
    latest = (
        sa.select(
            TableHealthSample.catalog_id.label("cid"),
            TableHealthSample.schema_name.label("sn"),
            TableHealthSample.table_name.label("tn"),
            sa.func.max(TableHealthSample.scanned_at).label("mx"),
        )
        .group_by(
            TableHealthSample.catalog_id,
            TableHealthSample.schema_name,
            TableHealthSample.table_name,
        )
        .subquery()
    )
    rows = (
        await db.execute(
            sa.select(
                TableHealthSample.catalog_id,
                TableHealthSample.schema_name,
                TableHealthSample.table_name,
                TableHealthSample.snapshot_id,
                TableHealthSample.scanned_at,
            ).join(
                latest,
                sa.and_(
                    TableHealthSample.catalog_id == latest.c.cid,
                    TableHealthSample.schema_name == latest.c.sn,
                    TableHealthSample.table_name == latest.c.tn,
                    TableHealthSample.scanned_at == latest.c.mx,
                ),
            )
        )
    ).all()
    return {(r[0], r[1], r[2]): (r[3], r[4]) for r in rows}


async def _filter_changed(
    db: AsyncSession,
    polaris: PolarisClient,
    candidates: list[tuple[Catalog, Workspace, str, str]],
    max_sample_age: timedelta,
) -> list[tuple[Catalog, Workspace, str, str]]:
    """Drop tables whose latest snapshot id is unchanged since the last sample.

    A table is re-probed only when its snapshot id changed (or it was never
    sampled), with a max-age safety net so a table that never changes still gets
    re-checked periodically. The snapshot id is read from Polaris (a metadata
    read, no table scan); on a Polaris error we keep the table rather than skip.
    """
    prior = await _latest_snapshot_ids(db)
    now = datetime.now(tz=UTC)

    keep: list[tuple[Catalog, Workspace, str, str]] = []
    to_check: list[tuple[Catalog, Workspace, str, str, int]] = []
    for catalog, ws, schema, table in candidates:
        key = (catalog.id, schema, table)
        if key not in prior:
            keep.append((catalog, ws, schema, table))  # never sampled
            continue
        prior_snapshot_id, scanned_at = prior[key]
        if prior_snapshot_id is None or scanned_at is None:
            keep.append((catalog, ws, schema, table))  # no usable prior sample
            continue
        if scanned_at.tzinfo is None:
            scanned_at = scanned_at.replace(tzinfo=UTC)
        if now - scanned_at >= max_sample_age:
            keep.append((catalog, ws, schema, table))  # safety net: too old
            continue
        to_check.append((catalog, ws, schema, table, prior_snapshot_id))

    async def _unchanged(item: tuple[Catalog, Workspace, str, str, int]) -> bool:
        catalog, _ws, schema, table, prior_snapshot_id = item
        try:
            snapshots = await polaris.list_snapshots(catalog.polaris_name, schema, table)
        except PolarisError as exc:
            logger.warning(
                "Maintenance scan: snapshot check failed for %s.%s: %s", schema, table, exc
            )
            return False  # keep (re-probe rather than skip)
        return bool(snapshots) and snapshots[0].snapshot_id == prior_snapshot_id

    results = await asyncio.gather(*(_unchanged(item) for item in to_check))
    for item, unchanged in zip(to_check, results):
        if not unchanged:
            keep.append(item[:4])
    return keep


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
        # Mirror leadership into the metrics layer so only the leader emits the
        # cluster-wide scanner gauges (no double-counting across replicas).
        set_scan_leader(is_leader)
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
