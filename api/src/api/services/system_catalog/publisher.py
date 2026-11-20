"""Copies the audit log, access, query history, lineage, compute, jobs, storage,
the semantic layer and assistant usage into the system catalog store.

Every ``SYSTEM_CATALOG_PUBLISH_INTERVAL_S`` one replica, whichever takes the
advisory lock, runs a tick: one transaction on the store that copies what changed
and records how far each copy has got, so a tick that fails leaves no partial
copy and the next one starts where the last committed one stopped.

- **access.audit** is exact. An event carries the id of the transaction that wrote
  it (``txid``), and a tick copies only events below the oldest transaction still
  running anywhere, ordered by ``(txid, id)``. Every such transaction has finished,
  and any event committed later has a higher ``txid``, so a transaction that
  commits late (deleting a workspace, or creating a catalog while Polaris answers)
  is never skipped, and nothing is copied twice. Transaction ids belong to one
  Postgres cluster: events restored from a dump into a new cluster can carry ids
  it has not reached, so those are copied as they are, and a cursor from the old
  cluster starts the copy over.
- **query.history** copies finished runs in ``(finished_at, id)`` order. A run's
  ``finished_at`` comes from the clock of the replica that finished it, so once a
  minute a tick re-reads the 15 minutes before the newest run copied, to catch a
  run committed after a later one. A run that changes after it was copied is
  updated in place, and each run written works out the tables it read and wrote
  into **access.table_access**. When the table gains columns (``HISTORY_VERSION``),
  the runs already copied are copied again, a batch a tick from the oldest, so they
  gain them too; new runs keep being copied meanwhile.
- **access.grants** is replaced whole, after a tick copies an access or identity
  event and at least every 5 minutes, which covers what changes access without an
  event (deleting an agent takes its grants with it). So are **access.users**,
  **access.workspaces**, **access.credentials**, ``info_schema.catalogs`` and the
  ``_acl`` tables the row filters consult, from the same read.
- **lineage.table_lineage** and **lineage.column_lineage** are replaced whole every
  5 minutes.
- **compute.agent_events**, **compute.agent_utilization**, **query.sessions**,
  **storage.table_health**, **ai.assistant_usage** and **ai.tool_calls** are copied
  like query.history, in order of the time each
  row records, with the last 15 minutes re-read once a minute. A session the copy
  still holds as live is re-read every tick until it ends. They outlive the
  source's rows, for ``SYSTEM_CATALOG_RETENTION_DAYS``.
- **storage.maintenance_operations** is read whole every tick: the source keeps
  only each recommendation's latest run, and the copy keeps one row per run.
- **compute.agents**, **jobs.schedules**, **storage.backends** and the semantic
  layer's ``info_schema`` tables, with the ``_acl`` table their row filter reads,
  are replaced whole every minute.

A tick copies at most ``BATCH`` rows of each kind; while any is behind, the next
tick starts at once instead of after the interval.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Row
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession, async_sessionmaker
from sqlalchemy.orm import aliased

from api.config import settings
from api.models.agent import Agent, AgentLifecycleEvent, AgentMetricsMinute
from api.models.agent_grant import AgentGrant
from api.models.assistant import AssistantConversation, AssistantMessage, AssistantToolCall
from api.models.audit import AuditEvent
from api.models.catalog import Catalog, WorkspaceCatalog
from api.models.catalog_grant import CatalogGrant
from api.models.lineage import LineageColumnEdge, LineageEdge
from api.models.maintenance import MaintenanceRecommendation, TableHealthSample
from api.models.query import Query, SavedQuery, Schedule
from api.models.semantic import (
    SemanticDataset,
    SemanticDimension,
    SemanticMetric,
    SemanticModel,
    SemanticRelationship,
)
from api.models.sql_session import SqlSession
from api.models.storage_backend import StorageBackend
from api.models.user import SERVICE_ACCOUNT_PROVIDER, Credential, User
from api.models.workspace import Workspace, WorkspaceMember
from api.services.query_history import HIDDEN_ORIGINS, profile_expr
from api.services.system_catalog import store
from api.services.system_catalog.table_access import accesses
from api.services.workspace import DEFAULT_SCHEMA

logger = logging.getLogger(__name__)

# Distinct advisory-lock key in the "dhs" family ('dhsy': system catalog publish).
_TICK_LOCK_KEY = 0x64687379
BATCH = 5000
GRANTS_REFRESH = timedelta(minutes=5)
LINEAGE_REFRESH = timedelta(minutes=5)
SNAPSHOT_REFRESH = timedelta(minutes=1)
# Bumped when query.history gains columns, to copy every run again.
HISTORY_VERSION = 2
HISTORY_SWEEP_EVERY = timedelta(minutes=1)
HISTORY_LATE_WINDOW = timedelta(minutes=15)
PURGE_EVERY = timedelta(hours=1)
TERMINAL_STATUSES = ("done", "failed", "cancelled")
# Changing any of these can change who reaches what, or what access.credentials holds.
_ACCESS_CATEGORIES = frozenset({"access", "identity", "credential"})
# The cursor rows and the tables whose lag they report.
LAGGED_TABLES = {"audit": "access.audit", "grants": "access.grants", "history": "query.history"}
_ZERO = uuid.UUID(int=0)


async def publish_once(
    session_factory: async_sessionmaker[AsyncSession], *, now: datetime | None = None
) -> bool:
    """Run one tick; return whether more is waiting than one tick copies.

    Returns False without copying when another replica holds the tick.
    """
    now = now or datetime.now(UTC)
    async with store.get_engine().begin() as target:
        locked = await target.scalar(
            sa.text("SELECT pg_try_advisory_xact_lock(:k)"), {"k": _TICK_LOCK_KEY}
        )
        if not locked:
            return False
        state: dict[str, dict[str, Any]] = dict(
            (await target.execute(sa.select(store.CURSORS.c.name, store.CURSORS.c.value))).all()
        )
        async with session_factory() as source:
            access_changed, audit_more = await _copy_audit(source, target, state, now)
            history_more = await _copy_history(source, target, state, now)
            ops_more = await _copy_operations(source, target, state, now)
        if access_changed or _due(state, "grants", "synced_at", GRANTS_REFRESH, now):
            await _refresh_grants(session_factory, target, now)
            state["grants"] = {"synced_at": now.isoformat()}
        if _due(state, "lineage", "synced_at", LINEAGE_REFRESH, now):
            await _refresh_lineage(session_factory, target, now)
            state["lineage"] = {"synced_at": now.isoformat()}
        if _due(state, "snapshots", "synced_at", SNAPSHOT_REFRESH, now):
            async with session_factory() as source:
                # One view, so no dataset is read without its model.
                await source.connection(execution_options={"isolation_level": "REPEATABLE READ"})
                await _replace(
                    target,
                    {**await snapshot_rows(source, now), **await semantic_rows(source, now)},
                )
            state["snapshots"] = {"synced_at": now.isoformat()}
        if _due(state, "purge", "at", PURGE_EVERY, now):
            await _purge(target, now)
            state["purge"] = {"at": now.isoformat()}
        for name, value in state.items():
            stmt = pg_insert(store.CURSORS).values(name=name, value=value)
            await target.execute(
                stmt.on_conflict_do_update(
                    index_elements=["name"],
                    set_={"value": stmt.excluded["value"], "updated_at": now},
                )
            )
    return audit_more or history_more or ops_more


async def lag_seconds(now: datetime | None = None) -> dict[str, float]:
    """How far behind each table of the copy is, in seconds, read from the store.

    A table never copied yet is left out.
    """
    now = now or datetime.now(UTC)
    async with store.get_engine().connect() as conn:
        rows = await conn.execute(
            sa.select(store.CURSORS.c.name, store.CURSORS.c.value).where(
                store.CURSORS.c.name.in_(LAGGED_TABLES)
            )
        )
        synced = {name: value.get("synced_at") for name, value in rows}
    return {
        LAGGED_TABLES[name]: max(0.0, (now - datetime.fromisoformat(at)).total_seconds())
        for name, at in synced.items()
        if at
    }


async def publisher_loop(session_factory: async_sessionmaker[AsyncSession]) -> None:
    """Background loop: create the store if it is missing, then publish each interval.

    A failing tick is logged once and retried; it also has the next tick re-run
    `store.ensure()`, which repairs a store whose database was recreated.
    """
    interval = settings.system_catalog_publish_interval_s
    logger.info("System catalog publisher started (every %ss)", interval)
    failing = False
    while True:
        more = False
        try:
            if store.is_available() or await store.ensure(session_factory):
                more = await publish_once(session_factory)
                if failing:
                    logger.info("System catalog publishing recovered")
                    failing = False
        except Exception:  # noqa: BLE001 - the loop must survive any tick failure
            store.mark_stale()
            if not failing:
                logger.exception("System catalog publish failed; retrying every %ss", interval)
            failing = True
        await asyncio.sleep(0 if more else interval)


# ── access.audit ──────────────────────────────────────────────────────────────

_AUDIT_COLUMNS = [c.name for c in store.AUDIT.columns]


# The first transaction id this snapshot has not seen assigned. No row written in
# this cluster has one as high; a row restored from another cluster's dump can.
_NEXT_TXID = sa.literal_column("pg_snapshot_xmax(pg_current_snapshot())::text::bigint")


async def _copy_audit(
    source: AsyncSession, target: AsyncConnection, state: dict, now: datetime
) -> tuple[bool, bool]:
    """Copy committed events past the cursor; return (an access change, more waiting)."""
    cursor = state.get("audit", {})
    after = _position(cursor, "txid", "id")
    restored = _position(cursor, "restored_txid", "restored_id")
    # The oldest transaction still running: everything below it has finished.
    horizon, next_txid = (
        await source.execute(
            sa.text(
                "SELECT pg_snapshot_xmin(s)::text::bigint, pg_snapshot_xmax(s)::text::bigint"
                " FROM pg_current_snapshot() s"
            )
        )
    ).one()
    if after[0] >= next_txid:
        # A cursor this cluster's ids have not reached was written against another
        # one: the databases were restored into a new cluster. Copy everything
        # again; an event already copied is skipped.
        after = restored = (0, _ZERO)
    rows = await _audit_rows(source, AuditEvent.txid < horizon, after, BATCH)
    if rows:
        after = (rows[-1].txid, rows[-1].id)
    # Restored events with ids above this cluster's are committed; copy them as they are.
    extra = await _audit_rows(source, AuditEvent.txid >= _NEXT_TXID, restored, BATCH - len(rows))
    if extra:
        restored = (extra[-1].txid, extra[-1].id)
    if rows or extra:
        await target.execute(
            pg_insert(store.AUDIT).on_conflict_do_nothing(index_elements=["id"]),
            [{c: getattr(r, c) for c in _AUDIT_COLUMNS} for r in rows + extra],
        )
    # Behind by as much as the oldest event not yet copied. Every event this
    # statement sees has committed; one still being written is not late.
    waiting = [
        t
        for t in (
            await _oldest_uncopied(source, target, AuditEvent.txid < _NEXT_TXID, after),
            await source.scalar(
                sa.select(sa.func.min(AuditEvent.event_time)).where(
                    AuditEvent.txid >= _NEXT_TXID, _after(AuditEvent.txid, AuditEvent.id, restored)
                )
            ),
        )
        if t is not None
    ]
    state["audit"] = {
        "txid": after[0],
        "id": str(after[1]),
        "restored_txid": restored[0],
        "restored_id": str(restored[1]),
        "synced_at": min([*waiting, now]).isoformat(),
    }
    access_changed = any(r.category in _ACCESS_CATEGORIES for r in rows + extra)
    return access_changed, len(rows) + len(extra) == BATCH


async def _audit_rows(
    source: AsyncSession,
    where: sa.ColumnElement[bool],
    after: tuple[int, uuid.UUID],
    limit: int,
) -> list[Row]:
    if limit <= 0:
        return []
    return list(
        (
            await source.execute(
                sa.select(*(getattr(AuditEvent, c) for c in _AUDIT_COLUMNS), AuditEvent.txid)
                .where(where, _after(AuditEvent.txid, AuditEvent.id, after))
                .order_by(AuditEvent.txid, AuditEvent.id)
                .limit(limit)
            )
        ).all()
    )


async def _oldest_uncopied(
    source: AsyncSession,
    target: AsyncConnection,
    where: sa.ColumnElement[bool],
    after: tuple[int, uuid.UUID],
    limit: int = 1000,
) -> datetime | None:
    """The time of the earliest event past ``after`` that is not in the copy.

    An event past the cursor is there already only when the restored-event pass
    copied it before this cluster's ids caught up with it. When the first
    ``limit`` are all such, this returns the last one's time: the lag reads high
    for a moment, never low.
    """
    page = (
        await source.execute(
            sa.select(AuditEvent.id, AuditEvent.event_time)
            .where(where, _after(AuditEvent.txid, AuditEvent.id, after))
            .order_by(AuditEvent.event_time, AuditEvent.id)
            .limit(limit)
        )
    ).all()
    if not page:
        return None
    copied = set(
        await target.scalars(
            sa.select(store.AUDIT.c.id).where(store.AUDIT.c.id.in_([r.id for r in page]))
        )
    )
    uncopied = [r.event_time for r in page if r.id not in copied]
    if uncopied:
        return uncopied[0]
    return page[-1].event_time if len(page) == limit else None


# ── query.history ─────────────────────────────────────────────────────────────


def history_select() -> sa.Select:
    """Finished runs people asked for, with the names they had when copied."""
    on_behalf = aliased(User)
    return (
        sa.select(
            Query.id,
            Query.workspace_id,
            Workspace.slug.label("workspace_slug"),
            Query.user_id,
            User.email.label("user_email"),
            Query.on_behalf_of_id,
            on_behalf.email.label("on_behalf_of_email"),
            Query.agent_id,
            Agent.name.label("agent_name"),
            Query.runtime_id,
            Query.origin,
            Query.statement_type,
            Query.status,
            Query.error,
            Query.sql,
            Query.row_count,
            Query.duration_ms,
            Query.result_bytes,
            Query.started_at,
            Query.dispatched_at,
            Query.running_at,
            Query.finished_at,
            Query.session_id,
            Query.schedule_id,
            Query.client_name,
            Query.client_version,
            Query.tags,
            Query.active_catalog,
            Query.active_schema,
            Query.cache_status,
            Query.result_source_query_id,
            *(
                profile_expr(field).label(field)
                for field in ("cpu_time_ms", "bytes_read", "peak_memory_bytes", "spill_bytes")
            ),
        )
        .select_from(Query)
        .outerjoin(Workspace, Workspace.id == Query.workspace_id)
        .outerjoin(User, User.id == Query.user_id)
        .outerjoin(on_behalf, on_behalf.id == Query.on_behalf_of_id)
        .outerjoin(Agent, Agent.id == Query.agent_id)
        .where(
            Query.finished_at.is_not(None),
            Query.status.in_(TERMINAL_STATUSES),
            # Machinery, not anyone's work: History hides the same runs.
            sa.or_(Query.origin.is_(None), Query.origin.not_in(HIDDEN_ORIGINS)),
        )
    )


async def history_rows(
    source: AsyncSession,
    *,
    after: tuple[datetime, uuid.UUID] | None = None,
    through: tuple[datetime, uuid.UUID] | None = None,
    floor: datetime | None = None,
    limit: int = BATCH,
) -> list[Row]:
    """Runs past ``after`` and up to ``through``, finished at or after ``floor``."""
    stmt = history_select()
    if after is not None:
        stmt = stmt.where(_after(Query.finished_at, Query.id, after))
    if through is not None:
        stmt = stmt.where(~_after(Query.finished_at, Query.id, through))
    if floor is not None:
        stmt = stmt.where(Query.finished_at >= floor)
    return list(
        (await source.execute(stmt.order_by(Query.finished_at, Query.id).limit(limit))).all()
    )


async def _copy_history(
    source: AsyncSession, target: AsyncConnection, state: dict, now: datetime
) -> bool:
    """Copy runs finished past the cursor, and sweep for late ones; return more waiting."""
    cursor = state.get("history", {})
    if cursor.get("version", 1) != HISTORY_VERSION:
        # Runs copied before the table gained columns lack them: copy those again,
        # alongside the new ones.
        cursor = {
            **cursor,
            "version": HISTORY_VERSION,
            "rewrite_through": (
                [cursor["finished_at"], cursor["id"]] if cursor.get("finished_at") else None
            ),
            "rewrite_after": None,
        }
    days = settings.system_catalog_query_history_days
    floor = now - timedelta(days=days) if days else None
    after = (
        (datetime.fromisoformat(cursor["finished_at"]), uuid.UUID(cursor["id"]))
        if cursor.get("finished_at")
        else None
    )
    rows = await history_rows(source, after=after, floor=floor)
    await _record_table_access(source, target, await _upsert_history(target, rows))
    if rows:
        after = (rows[-1].finished_at, rows[-1].id)
    more = len(rows) == BATCH

    swept_at = cursor.get("swept_at")
    if not more and after and _older_than(swept_at, now, HISTORY_SWEEP_EVERY):
        page_after = (after[0] - HISTORY_LATE_WINDOW, _ZERO)
        while page := await history_rows(source, after=page_after, through=after, floor=floor):
            await _record_table_access(source, target, await _upsert_history(target, page))
            if len(page) < BATCH:
                break
            page_after = (page[-1].finished_at, page[-1].id)
        swept_at = now.isoformat()

    # One batch a tick, oldest first, written whether or not it looks unchanged.
    rewrite_through, rewrite_after = cursor.get("rewrite_through"), cursor.get("rewrite_after")
    if rewrite_through:
        page = await history_rows(
            source,
            after=_run_position(rewrite_after),
            through=_run_position(rewrite_through),
            floor=floor,
        )
        await _record_table_access(source, target, await _upsert_history(target, page, True))
        if len(page) < BATCH:
            rewrite_through = rewrite_after = None
        else:
            rewrite_after = [page[-1].finished_at.isoformat(), str(page[-1].id)]

    state["history"] = {
        "version": HISTORY_VERSION,
        "rewrite_through": rewrite_through,
        "rewrite_after": rewrite_after,
        "finished_at": after[0].isoformat() if after else None,
        "id": str(after[1]) if after else None,
        "swept_at": swept_at,
        # Caught up means every run that had finished when the tick began.
        "synced_at": cursor.get("synced_at") if more else now.isoformat(),
    }
    return more


def _run_position(value: list[str] | None) -> tuple[datetime, uuid.UUID] | None:
    return (datetime.fromisoformat(value[0]), uuid.UUID(value[1])) if value else None


async def _upsert_history(
    target: AsyncConnection, rows: list[Row], rewrite: bool = False
) -> list[Row]:
    """Insert or update ``rows``; return those written. A re-read of an unchanged
    run writes nothing, unless ``rewrite``."""
    if not rows:
        return []
    stmt = pg_insert(store.HISTORY)
    columns = [c.name for c in store.HISTORY.columns if c.name != "id"]
    changed = sa.tuple_(*(store.HISTORY.c[c] for c in columns)).is_distinct_from(
        sa.tuple_(*(stmt.excluded[c] for c in columns))
    )
    written = set(
        await target.scalars(
            stmt.on_conflict_do_update(
                index_elements=["id"],
                set_={c: stmt.excluded[c] for c in columns},
                where=None if rewrite else changed,
            ).returning(store.HISTORY.c.id),
            [dict(r._mapping) for r in rows],
        )
    )
    return [r for r in rows if r.id in written]


async def _record_table_access(
    source: AsyncSession, target: AsyncConnection, rows: list[Row]
) -> None:
    """Replace the access.table_access rows of each run in ``rows``."""
    if not rows:
        return
    await target.execute(
        sa.delete(store.TABLE_ACCESS).where(store.TABLE_ACCESS.c.query_id.in_([r.id for r in rows]))
    )
    done = [r for r in rows if r.status == "done"]
    if not done:
        return
    catalogs = {
        slug.lower(): (id_, slug)
        for id_, slug in await source.execute(sa.select(Catalog.id, Catalog.slug))
    }
    defaults = dict(
        (
            await source.execute(
                sa.select(WorkspaceCatalog.workspace_id, Catalog.slug)
                .join(Catalog, Catalog.id == WorkspaceCatalog.catalog_id)
                .where(
                    WorkspaceCatalog.is_default.is_(True),
                    WorkspaceCatalog.workspace_id.in_({r.workspace_id for r in done}),
                )
            )
        ).all()
    )

    def parse() -> list[dict[str, Any]]:
        return [
            {
                "query_id": r.id,
                "catalog_id": catalogs[a.catalog_name.lower()][0],
                "catalog_name": a.catalog_name,
                "schema_name": a.schema_name,
                "table_name": a.table_name,
                "access": a.access,
                "finished_at": r.finished_at,
                "workspace_id": r.workspace_id,
                "workspace_slug": r.workspace_slug,
                "user_id": r.user_id,
                "user_email": r.user_email,
                "on_behalf_of_id": r.on_behalf_of_id,
                "on_behalf_of_email": r.on_behalf_of_email,
            }
            for r in done
            for a in accesses(
                r.sql,
                r.active_catalog or defaults.get(r.workspace_id),
                r.active_schema if r.active_catalog else DEFAULT_SCHEMA,
                catalogs,
            )
        ]

    # Parsing is CPU-bound; a thread keeps the API's event loop answering meanwhile.
    if found := await asyncio.to_thread(parse):
        await target.execute(sa.insert(store.TABLE_ACCESS), found)


# ── access.grants ─────────────────────────────────────────────────────────────

_GRANT_COLUMNS = [c.name for c in store.GRANTS.columns]


def _principal_type(auth_provider: str) -> str:
    return "service_account" if auth_provider == SERVICE_ACCOUNT_PROVIDER else "user"


def _principal(user: Any) -> dict[str, Any]:
    return {
        "principal_type": _principal_type(user.auth_provider),
        "principal_id": user.user_id,
        "principal_email": user.email,
        "principal_active": user.is_active,
    }


async def grant_rows(source: AsyncSession, now: datetime) -> list[dict[str, Any]]:
    """Every grant of every kind, as rows of access.grants."""
    granter = aliased(User)
    who = (
        User.id.label("user_id"),
        User.email,
        User.auth_provider,
        User.is_active,
    )
    rows: list[dict[str, Any]] = []

    def add(kind: str, level: str, **fields: Any) -> None:
        rows.append(
            {
                **dict.fromkeys(_GRANT_COLUMNS),
                "kind": kind,
                "level": level,
                "snapshot_time": now,
                **fields,
            }
        )

    for r in await source.execute(sa.select(*who, User.role)):
        add("global_role", r.role, **_principal(r))

    for r in await source.execute(
        sa.select(*who, WorkspaceMember.role, Workspace.id.label("ws_id"), Workspace.slug)
        .select_from(WorkspaceMember)
        .join(User, User.id == WorkspaceMember.user_id)
        .join(Workspace, Workspace.id == WorkspaceMember.workspace_id)
    ):
        add(
            "workspace_member", r.role, workspace_id=r.ws_id, workspace_slug=r.slug, **_principal(r)
        )

    for r in await source.execute(
        sa.select(
            WorkspaceCatalog.access_mode,
            WorkspaceCatalog.attached_at,
            Workspace.id.label("ws_id"),
            Workspace.slug.label("ws_slug"),
            Catalog.id.label("cat_id"),
            Catalog.slug.label("cat_slug"),
            granter.id.label("by_id"),
            granter.email.label("by_email"),
        )
        .select_from(WorkspaceCatalog)
        .join(Workspace, Workspace.id == WorkspaceCatalog.workspace_id)
        .join(Catalog, Catalog.id == WorkspaceCatalog.catalog_id)
        .outerjoin(granter, granter.id == WorkspaceCatalog.attached_by)
    ):
        add(
            "catalog_attachment",
            r.access_mode,
            workspace_id=r.ws_id,
            workspace_slug=r.ws_slug,
            catalog_id=r.cat_id,
            catalog_slug=r.cat_slug,
            granted_by_id=r.by_id,
            granted_by_email=r.by_email,
            granted_at=r.attached_at,
        )

    for r in await source.execute(
        sa.select(
            *who,
            CatalogGrant.tier,
            CatalogGrant.schema_name,
            CatalogGrant.table_name,
            CatalogGrant.created_at,
            Catalog.id.label("cat_id"),
            Catalog.slug.label("cat_slug"),
            granter.id.label("by_id"),
            granter.email.label("by_email"),
        )
        .select_from(CatalogGrant)
        .join(User, User.id == CatalogGrant.user_id)
        .join(Catalog, Catalog.id == CatalogGrant.catalog_id)
        .outerjoin(granter, granter.id == CatalogGrant.created_by)
    ):
        add(
            "catalog_grant",
            r.tier,
            catalog_id=r.cat_id,
            catalog_slug=r.cat_slug,
            schema_name=r.schema_name,
            table_name=r.table_name,
            granted_by_id=r.by_id,
            granted_by_email=r.by_email,
            granted_at=r.created_at,
            **_principal(r),
        )

    for r in await source.execute(sa.select(Agent.id, Agent.name, Agent.access_mode)):
        add("agent_access_mode", r.access_mode, agent_id=r.id, agent_name=r.name)

    for r in await source.execute(
        sa.select(
            AgentGrant.tier,
            AgentGrant.created_at,
            Agent.id.label("agent_id"),
            Agent.name.label("agent_name"),
            User.id.label("user_id"),
            User.email,
            User.auth_provider,
            User.is_active,
            Workspace.id.label("ws_id"),
            Workspace.slug.label("ws_slug"),
            granter.id.label("by_id"),
            granter.email.label("by_email"),
        )
        .select_from(AgentGrant)
        .join(Agent, Agent.id == AgentGrant.agent_id)
        .outerjoin(User, User.id == AgentGrant.user_id)
        .outerjoin(Workspace, Workspace.id == AgentGrant.workspace_id)
        .outerjoin(granter, granter.id == AgentGrant.created_by)
    ):
        # A grant to a workspace reaches each of its members.
        principal = (
            _principal(r)
            if r.user_id
            else {
                "principal_type": "workspace",
                "workspace_id": r.ws_id,
                "workspace_slug": r.ws_slug,
            }
        )
        add(
            "agent_grant",
            r.tier,
            agent_id=r.agent_id,
            agent_name=r.agent_name,
            granted_by_id=r.by_id,
            granted_by_email=r.by_email,
            granted_at=r.created_at,
            **principal,
        )

    for r in await source.execute(
        sa.select(Workspace.id, Workspace.slug, Agent.id.label("agent_id"), Agent.name).join(
            Agent, Agent.id == Workspace.default_agent_id
        )
    ):
        # The workspace's default compute: every member may run on it.
        add(
            "workspace_default_agent",
            "use",
            workspace_id=r.id,
            workspace_slug=r.slug,
            agent_id=r.agent_id,
            agent_name=r.name,
        )
    return rows


async def visibility_rows(source: AsyncSession) -> dict[sa.Table, list[dict[str, Any]]]:
    """What decides who sees what in info_schema: each workspace's catalog
    attachments, its members, and every catalog grant."""
    attachments = (
        await source.execute(
            sa.select(
                WorkspaceCatalog.workspace_id,
                WorkspaceCatalog.catalog_id,
                WorkspaceCatalog.access_mode,
                WorkspaceCatalog.is_default,
                WorkspaceCatalog.attached_at,
                Catalog.slug,
                Catalog.kind,
                Catalog.created_at,
            ).join(Catalog, Catalog.id == WorkspaceCatalog.catalog_id)
        )
    ).all()
    members = await source.execute(sa.select(WorkspaceMember.workspace_id, WorkspaceMember.user_id))
    scopes = await source.execute(
        sa.select(
            CatalogGrant.user_id,
            CatalogGrant.catalog_id,
            CatalogGrant.schema_name,
            CatalogGrant.table_name,
        )
    )
    return {
        store.INFO_CATALOGS: [
            {
                "workspace_id": r.workspace_id,
                "catalog_id": r.catalog_id,
                "catalog_name": r.slug,
                "catalog_kind": r.kind,
                "access_mode": r.access_mode,
                "is_default": r.is_default,
                "created_at": r.created_at,
                "attached_at": r.attached_at,
            }
            for r in attachments
        ],
        store.ACL_ATTACHMENTS: [
            {
                "workspace_id": r.workspace_id,
                "catalog_id": r.catalog_id,
                "access_mode": r.access_mode,
            }
            for r in attachments
        ],
        store.ACL_MEMBERS: [dict(r._mapping) for r in members],
        store.ACL_SCOPES: [dict(r._mapping) for r in scopes],
    }


async def identity_rows(source: AsyncSession, now: datetime) -> dict[sa.Table, list[dict]]:
    """Every user, workspace and personal access token, never a secret or a hash.

    The assistant's per-turn tokens, which live for one answer and are recorded on
    each event made with them, are left out.
    """
    default_agent = aliased(Agent)
    users = await source.execute(
        sa.select(
            User.id,
            User.email,
            User.name,
            User.auth_provider,
            User.role,
            User.is_active,
            User.created_at,
        )
    )
    workspaces = await source.execute(
        sa.select(Workspace, default_agent.name.label("agent_name")).outerjoin(
            default_agent, default_agent.id == Workspace.default_agent_id
        )
    )
    tokens = await source.execute(
        sa.select(
            Credential.id,
            Credential.kind,
            Credential.created_at,
            Credential.expires_at,
            User.id.label("user_id"),
            User.email,
            User.auth_provider,
            User.is_active,
        )
        .join(User, User.id == Credential.user_id)
        .where(Credential.kind == "pat", Credential.on_behalf_of_id.is_(None))
    )
    return {
        store.USERS: [
            {
                "user_id": r.id,
                "email": r.email,
                "name": r.name,
                "principal_type": _principal_type(r.auth_provider),
                "auth_provider": r.auth_provider,
                "global_role": r.role,
                "is_active": r.is_active,
                "created_at": r.created_at,
                "snapshot_time": now,
            }
            for r in users
        ],
        store.WORKSPACES: [
            {
                "workspace_id": w.id,
                "workspace_slug": w.slug,
                "name": w.name,
                "description": w.description,
                "timezone": w.timezone,
                "default_agent_id": w.default_agent_id,
                "default_agent_name": agent_name,
                "result_cache_enabled": w.result_cache_enabled,
                "created_at": w.created_at,
                "snapshot_time": now,
            }
            for w, agent_name in workspaces
        ],
        store.CREDENTIALS: [
            {
                "credential_id": r.id,
                "kind": r.kind,
                **_principal(r),
                "created_at": r.created_at,
                "expires_at": r.expires_at,
                "snapshot_time": now,
            }
            for r in tokens
        ],
    }


async def _refresh_grants(
    session_factory: async_sessionmaker[AsyncSession], target: AsyncConnection, now: datetime
) -> None:
    """Replace access.grants, the users, workspaces and tokens beside it, and what
    info_schema's row filters consult, with a snapshot read in one consistent view."""
    async with session_factory() as snapshot:
        await snapshot.connection(execution_options={"isolation_level": "REPEATABLE READ"})
        tables = {
            store.GRANTS: await grant_rows(snapshot, now),
            **await identity_rows(snapshot, now),
            **await visibility_rows(snapshot),
        }
    await _replace(target, tables)


async def _replace(target: AsyncConnection, tables: dict[sa.Table, list[dict]]) -> None:
    for table, rows in tables.items():
        # Not TRUNCATE: that would block readers until the tick commits.
        await target.execute(sa.delete(table))
        if rows:
            await target.execute(sa.insert(table), rows)


# ── lineage ───────────────────────────────────────────────────────────────────


async def lineage_rows(source: AsyncSession, now: datetime) -> dict[sa.Table, list[dict]]:
    """Every lineage edge, with the names of its catalogs and workspace."""
    source_catalog, target_catalog = aliased(Catalog), aliased(Catalog)
    names = (
        source_catalog.slug.label("source_catalog_name"),
        target_catalog.slug.label("target_catalog_name"),
    )

    def named(stmt: sa.Select) -> sa.Select:
        return stmt.outerjoin(
            source_catalog, source_catalog.id == LineageEdge.source_catalog_id
        ).outerjoin(target_catalog, target_catalog.id == LineageEdge.target_catalog_id)

    edges = await source.execute(
        named(
            sa.select(LineageEdge, *names, Workspace.slug.label("workspace_slug"))
            .select_from(LineageEdge)
            .outerjoin(Workspace, Workspace.id == LineageEdge.workspace_id)
        )
    )
    columns = await source.execute(
        named(
            sa.select(LineageColumnEdge, LineageEdge, *names)
            .select_from(LineageColumnEdge)
            .join(LineageEdge, LineageEdge.id == LineageColumnEdge.edge_id)
        )
    )

    def ends(edge: LineageEdge, source_name: str | None, target_name: str | None) -> dict:
        return {
            "source_catalog_id": edge.source_catalog_id,
            "source_catalog_name": source_name,
            "source_system": edge.source_system,
            "source_schema": edge.source_schema,
            "source_table": edge.source_table,
            "target_catalog_id": edge.target_catalog_id,
            "target_catalog_name": target_name,
            "target_system": edge.target_system,
            "target_schema": edge.target_schema,
            "target_table": edge.target_table,
        }

    return {
        store.TABLE_LINEAGE: [
            {
                "edge_id": e.id,
                "provider": e.provider,
                "operation": e.operation,
                "confidence": e.confidence,
                "column_lineage": e.column_lineage,
                **ends(e, source_name, target_name),
                "workspace_id": e.workspace_id,
                "workspace_slug": workspace_slug,
                "last_query_id": e.last_query_id,
                "first_seen_at": e.first_seen_at,
                "last_seen_at": e.last_seen_at,
                "observation_count": e.observation_count,
                "snapshot_time": now,
            }
            for e, source_name, target_name, workspace_slug in edges
        ],
        store.COLUMN_LINEAGE: [
            {
                "id": c.id,
                "edge_id": c.edge_id,
                **ends(e, source_name, target_name),
                "source_column": c.source_column,
                "target_column": c.target_column,
                "first_seen_at": c.first_seen_at,
                "last_seen_at": c.last_seen_at,
                "snapshot_time": now,
            }
            for c, e, source_name, target_name in columns
        ],
    }


async def _refresh_lineage(
    session_factory: async_sessionmaker[AsyncSession], target: AsyncConnection, now: datetime
) -> None:
    async with session_factory() as snapshot:
        await snapshot.connection(execution_options={"isolation_level": "REPEATABLE READ"})
        tables = await lineage_rows(snapshot, now)
    await _replace(target, tables)


# ── compute, sessions, table health and maintenance ───────────────────────────


@dataclass(frozen=True)
class _Feed:
    """A source table copied in ``(time, key)`` order into ``table``, whose
    ``columns`` hold that time and key."""

    name: str
    table: sa.Table
    time: Any
    key: Any
    columns: tuple[str, str]
    select: Callable[[], sa.Select]


def _agent_events() -> sa.Select:
    event = AgentLifecycleEvent
    return sa.select(
        event.id.label("event_id"),
        event.agent_id,
        Agent.name.label("agent_name"),
        event.event,
        event.reason,
        event.at.label("event_time"),
    ).join(Agent, Agent.id == event.agent_id)


def _agent_minutes() -> sa.Select:
    minute = AgentMetricsMinute
    return sa.select(
        minute.agent_id,
        minute.minute,
        Agent.name.label("agent_name"),
        minute.cpu_avg.label("cpu_avg_percent"),
        minute.cpu_max.label("cpu_max_percent"),
        minute.mem_avg.label("memory_avg_percent"),
        minute.mem_max.label("memory_max_percent"),
        minute.running_max.label("running_queries_max"),
        minute.queued_max.label("queued_queries_max"),
        minute.session_max.label("sessions_max"),
        minute.sample_count,
        minute.covered_s.label("covered_seconds"),
        minute.oom_kills,
    ).join(Agent, Agent.id == minute.agent_id)


def _sessions() -> sa.Select:
    session = SqlSession
    return (
        sa.select(
            session.id.label("session_id"),
            session.workspace_id,
            Workspace.slug.label("workspace_slug"),
            session.user_id,
            User.email.label("user_email"),
            session.agent_id,
            Agent.name.label("agent_name"),
            session.runtime_id,
            session.status,
            session.close_reason,
            session.error,
            session.client_name,
            session.client_version,
            session.active_catalog,
            session.use_cache,
            session.created_at,
            session.opened_at,
            session.last_active_at,
            session.closed_at,
        )
        .select_from(session)
        .join(Workspace, Workspace.id == session.workspace_id)
        .outerjoin(User, User.id == session.user_id)
        .outerjoin(Agent, Agent.id == session.agent_id)
    )


def _table_health() -> sa.Select:
    sample = TableHealthSample
    return (
        sa.select(
            sample.id.label("sample_id"),
            sample.scanned_at,
            sample.catalog_id,
            Catalog.slug.label("catalog_name"),
            sample.workspace_id,
            Workspace.slug.label("workspace_slug"),
            sample.schema_name,
            sample.table_name,
            sample.score,
            sample.snapshot_count,
            sample.snapshot_id,
            sample.oldest_snapshot_age_days,
            sample.data_file_count,
            sample.manifest_count,
            sample.total_data_bytes,
            sample.avg_file_bytes,
            sample.metadata_bytes,
            sample.orphan_bytes,
            sample.orphan_file_count,
            sample.small_file_ratio,
            sample.factors,
        )
        .select_from(sample)
        .join(Catalog, Catalog.id == sample.catalog_id)
        .outerjoin(Workspace, Workspace.id == sample.workspace_id)
    )


def _assistant_usage() -> sa.Select:
    message, conversation = AssistantMessage, AssistantConversation
    service_account = aliased(User)
    return (
        sa.select(
            message.id.label("message_id"),
            message.conversation_id,
            conversation.workspace_id,
            Workspace.slug.label("workspace_slug"),
            conversation.user_id,
            User.email.label("user_email"),
            conversation.service_account_id,
            service_account.email.label("service_account_email"),
            message.ordinal.label("turn"),
            message.input_tokens,
            message.output_tokens,
            message.created_at,
        )
        .select_from(message)
        .join(conversation, conversation.id == message.conversation_id)
        .join(Workspace, Workspace.id == conversation.workspace_id)
        .outerjoin(User, User.id == conversation.user_id)
        .outerjoin(service_account, service_account.id == conversation.service_account_id)
    )


def _tool_calls() -> sa.Select:
    """Which tool, and how it ended: never its arguments, detail or tables."""
    call, conversation = AssistantToolCall, AssistantConversation
    return (
        sa.select(
            call.id.label("call_id"),
            call.conversation_id,
            conversation.workspace_id,
            Workspace.slug.label("workspace_slug"),
            conversation.user_id,
            User.email.label("user_email"),
            call.tool,
            call.status,
            call.query_id,
            call.latency_ms,
            call.created_at,
        )
        .select_from(call)
        .join(conversation, conversation.id == call.conversation_id)
        .join(Workspace, Workspace.id == conversation.workspace_id)
        .outerjoin(User, User.id == conversation.user_id)
    )


_FEEDS = (
    _Feed(
        "agent_events",
        store.AGENT_EVENTS,
        AgentLifecycleEvent.at,
        AgentLifecycleEvent.id,
        ("event_time", "event_id"),
        _agent_events,
    ),
    _Feed(
        "agent_utilization",
        store.AGENT_UTILIZATION,
        AgentMetricsMinute.minute,
        AgentMetricsMinute.agent_id,
        ("minute", "agent_id"),
        _agent_minutes,
    ),
    _Feed(
        "sessions",
        store.SESSIONS,
        SqlSession.created_at,
        SqlSession.id,
        ("created_at", "session_id"),
        _sessions,
    ),
    _Feed(
        "table_health",
        store.TABLE_HEALTH,
        TableHealthSample.scanned_at,
        TableHealthSample.id,
        ("scanned_at", "sample_id"),
        _table_health,
    ),
    _Feed(
        "assistant_usage",
        store.ASSISTANT_USAGE,
        AssistantMessage.created_at,
        AssistantMessage.id,
        ("created_at", "message_id"),
        _assistant_usage,
    ),
    _Feed(
        "tool_calls",
        store.TOOL_CALLS,
        AssistantToolCall.created_at,
        AssistantToolCall.id,
        ("created_at", "call_id"),
        _tool_calls,
    ),
)
_SESSION_ENDED = ("closed", "expired", "failed")


async def _copy_operations(
    source: AsyncSession, target: AsyncConnection, state: dict, now: datetime
) -> bool:
    """Copy each feed, the sessions still live, and maintenance runs; return more waiting."""
    days = settings.system_catalog_retention_days
    floor = now - timedelta(days=days) if days else None
    more = False
    for feed in _FEEDS:
        more = await _copy_feed(source, target, state, feed, floor, now) or more
    if live := list(
        await target.scalars(
            sa.select(store.SESSIONS.c.session_id).where(
                store.SESSIONS.c.status.notin_(_SESSION_ENDED)
            )
        )
    ):
        sessions = await source.execute(_sessions().where(SqlSession.id.in_(live)))
        await _upsert(target, store.SESSIONS, list(sessions))
    await _upsert(target, store.MAINTENANCE_OPERATIONS, await maintenance_rows(source, floor))
    return more


async def _copy_feed(
    source: AsyncSession,
    target: AsyncConnection,
    state: dict,
    feed: _Feed,
    floor: datetime | None,
    now: datetime,
) -> bool:
    cursor = state.get(feed.name, {})
    after = _run_position(cursor.get("after"))
    order = (feed.time, feed.key)
    base = feed.select() if floor is None else feed.select().where(feed.time >= floor)
    new = list(
        await source.execute(
            (base if after is None else base.where(_after(*order, after)))
            .order_by(*order)
            .limit(BATCH)
        )
    )
    late: list[Row] = []
    swept_at = cursor.get("swept_at")
    if after and _older_than(swept_at, now, HISTORY_SWEEP_EVERY):
        late = list(
            await source.execute(
                base.where(feed.time >= after[0] - HISTORY_LATE_WINDOW, ~_after(*order, after))
            )
        )
        swept_at = now.isoformat()
    await _upsert(target, feed.table, [*new, *late])
    if new:
        last = new[-1]._mapping
        position = [last[feed.columns[0]].isoformat(), str(last[feed.columns[1]])]
    else:
        position = cursor.get("after")
    state[feed.name] = {"after": position, "swept_at": swept_at}
    return len(new) == BATCH


async def maintenance_rows(source: AsyncSession, floor: datetime | None) -> list[Row]:
    """Each recommendation's latest maintenance run, started at or after ``floor``."""
    rec = MaintenanceRecommendation
    stmt = (
        sa.select(
            rec.applied_query_id.label("query_id"),
            rec.id.label("recommendation_id"),
            rec.kind,
            rec.catalog_id,
            Catalog.slug.label("catalog_name"),
            rec.workspace_id,
            Workspace.slug.label("workspace_slug"),
            rec.schema_name,
            rec.table_name,
            rec.apply_status.label("status"),
            rec.apply_error.label("error"),
            rec.apply_result.label("result"),
            rec.applied_at.label("started_at"),
            Query.finished_at,
            rec.applied_by.label("applied_by_id"),
            User.email.label("applied_by_email"),
        )
        .select_from(rec)
        .join(Catalog, Catalog.id == rec.catalog_id)
        .outerjoin(Workspace, Workspace.id == rec.workspace_id)
        .outerjoin(Query, Query.id == rec.applied_query_id)
        .outerjoin(User, User.id == rec.applied_by)
        .where(rec.applied_query_id.is_not(None))
    )
    if floor is not None:
        stmt = stmt.where(rec.applied_at >= floor)
    return list(await source.execute(stmt))


async def snapshot_rows(source: AsyncSession, now: datetime) -> dict[sa.Table, list[dict]]:
    """Every agent, schedule and storage backend, as they are now."""
    agents = (await source.scalars(sa.select(Agent))).all()
    last_run, creator = aliased(Query), aliased(User)
    backends = await source.execute(
        sa.select(StorageBackend, creator.email, sa.func.count(Catalog.id))
        .select_from(StorageBackend)
        .outerjoin(creator, creator.id == StorageBackend.created_by)
        .outerjoin(Catalog, Catalog.storage_backend_id == StorageBackend.id)
        .group_by(StorageBackend.id, creator.email)
    )
    schedules = await source.execute(
        sa.select(
            Schedule,
            Workspace.slug,
            Workspace.timezone,
            SavedQuery.name,
            Agent.name,
            creator.email,
            last_run.status,
        )
        .select_from(Schedule)
        .join(Workspace, Workspace.id == Schedule.workspace_id)
        .outerjoin(SavedQuery, SavedQuery.id == Schedule.saved_query_id)
        .outerjoin(Agent, Agent.id == Schedule.agent_id)
        .outerjoin(creator, creator.id == Schedule.created_by)
        .outerjoin(last_run, last_run.id == Schedule.last_run_query_id)
    )
    return {
        store.AGENTS: [_agent_row(agent, now) for agent in agents],
        # Never `config`: what DuckHaven signs in to the backend with.
        store.STORAGE_BACKENDS: [
            {
                "backend_id": backend.id,
                "name": backend.name,
                "kind": backend.kind,
                "root_uri": backend.root_uri,
                "catalog_count": catalog_count,
                "created_by_id": backend.created_by,
                "created_by_email": created_by_email,
                "created_at": backend.created_at,
                "snapshot_time": now,
            }
            for backend, created_by_email, catalog_count in backends
        ],
        store.SCHEDULES: [
            {
                "schedule_id": schedule.id,
                "workspace_id": schedule.workspace_id,
                "workspace_slug": workspace_slug,
                "job_type": schedule.job_type,
                "saved_query_id": schedule.saved_query_id,
                "saved_query_name": saved_query_name,
                "agent_id": schedule.agent_id,
                "agent_name": agent_name,
                "cron": schedule.cron,
                "timezone": timezone,
                "enabled": schedule.enabled,
                "next_run_at": schedule.next_run_at,
                "last_run_at": schedule.last_run_at,
                "last_run_query_id": schedule.last_run_query_id,
                "last_run_status": last_run_status,
                "created_by_id": schedule.created_by,
                "created_by_email": created_by_email,
                "created_at": schedule.created_at,
                "snapshot_time": now,
            }
            for (
                schedule,
                workspace_slug,
                timezone,
                saved_query_name,
                agent_name,
                created_by_email,
                last_run_status,
            ) in schedules
        ],
    }


async def semantic_rows(source: AsyncSession, now: datetime) -> dict[sa.Table, list[dict]]:
    """Every semantic model and what it defines, and the tables its row filter checks.

    Sample values are left out: they are rows of the table, which seeing a model's
    definition does not let a member read.
    """
    owner = aliased(User)
    models = (
        await source.execute(
            sa.select(SemanticModel, owner.email).outerjoin(
                owner, owner.id == SemanticModel.owner_id
            )
        )
    ).all()
    datasets = (
        await source.execute(
            sa.select(SemanticDataset, Catalog.slug).outerjoin(
                Catalog, Catalog.id == SemanticDataset.catalog_id
            )
        )
    ).all()
    metrics = (
        await source.execute(
            sa.select(SemanticMetric, owner.email).outerjoin(
                owner, owner.id == SemanticMetric.owner_id
            )
        )
    ).all()
    dimensions = (await source.scalars(sa.select(SemanticDimension))).all()
    relationships = (await source.scalars(sa.select(SemanticRelationship))).all()
    of_model = {
        m.id: {"model_id": m.id, "workspace_id": m.workspace_id, "model_slug": m.slug}
        for m, _ in models
    }
    dataset_names = {d.id: d.name for d, _ in datasets}
    return {
        store.SEMANTIC_MODELS: [
            {
                **of_model[m.id],
                "name": m.name,
                "description": m.description,
                "status": m.status,
                "provider": m.provider,
                "owner_id": m.owner_id,
                "owner_email": owner_email,
                "created_at": m.created_at,
                "updated_at": m.updated_at,
                "refreshed_at": now,
            }
            for m, owner_email in models
        ],
        store.SEMANTIC_DATASETS: [
            {
                "dataset_id": d.id,
                **of_model[d.model_id],
                "name": d.name,
                "description": d.description,
                "catalog_id": d.catalog_id,
                "catalog_name": catalog_name,
                "schema_name": d.schema_name,
                "table_name": d.table_name,
                "primary_key": d.primary_key,
                "synonyms": d.synonyms,
                "validation_state": d.validation_state,
                "refreshed_at": now,
            }
            for d, catalog_name in datasets
        ],
        store.SEMANTIC_DIMENSIONS: [
            {
                "dimension_id": d.id,
                **of_model[d.model_id],
                "dataset_id": d.dataset_id,
                "dataset_name": dataset_names.get(d.dataset_id),
                "name": d.name,
                "display_name": d.display_name,
                "description": d.description,
                "kind": d.kind,
                "expr": d.expr,
                "data_type": d.data_type,
                "time_grains": d.time_grains,
                "is_default_time": d.is_default_time,
                "synonyms": d.synonyms,
                "validation_state": d.validation_state,
                "refreshed_at": now,
            }
            for d in dimensions
        ],
        store.SEMANTIC_METRICS: [
            {
                "metric_id": m.id,
                **of_model[m.model_id],
                "dataset_id": m.dataset_id,
                "dataset_name": dataset_names.get(m.dataset_id),
                "name": m.name,
                "display_name": m.display_name,
                "description": m.description,
                "agg": m.agg,
                "expr": m.expr,
                "filter": m.filter,
                "time_dimension_id": m.time_dimension_id,
                "format": m.format,
                "unit": m.unit,
                "currency": m.currency,
                "polarity": m.polarity,
                "caveat": m.caveat,
                "synonyms": m.synonyms,
                "status": m.status,
                "owner_id": m.owner_id,
                "owner_email": owner_email,
                "validation_state": m.validation_state,
                "created_at": m.created_at,
                "updated_at": m.updated_at,
                "refreshed_at": now,
            }
            for m, owner_email in metrics
        ],
        store.SEMANTIC_RELATIONSHIPS: [
            {
                "relationship_id": r.id,
                **of_model[r.model_id],
                "name": r.name,
                "left_dataset_id": r.left_dataset_id,
                "left_dataset_name": dataset_names.get(r.left_dataset_id),
                "right_dataset_id": r.right_dataset_id,
                "right_dataset_name": dataset_names.get(r.right_dataset_id),
                "join_columns": r.join_columns,
                "cardinality": r.cardinality,
                "validation_state": r.validation_state,
                "refreshed_at": now,
            }
            for r in relationships
        ],
        store.ACL_SEMANTIC_TABLES: [
            {
                "workspace_id": of_model[d.model_id]["workspace_id"],
                "model_id": d.model_id,
                "catalog_id": d.catalog_id,
                "schema_name": d.schema_name,
                "table_name": d.table_name,
            }
            for d, _ in datasets
        ],
    }


def _agent_row(agent: Agent, now: datetime) -> dict[str, Any]:
    # What the agent reported when it registered; empty until it has.
    reported = agent.capabilities or {}
    return {
        "agent_id": agent.id,
        "name": agent.name,
        "kind": "static" if agent.provider is None else "elastic",
        "provider": agent.provider,
        "status": agent.status,
        "lifecycle": agent.lifecycle,
        "access_mode": agent.access_mode,
        "runtime_id": reported.get("runtime_id") or agent.requested_runtime_id,
        "duckdb_version": reported.get("duckdb_version"),
        "agent_version": reported.get("agent_version"),
        "cores": reported.get("cores"),
        "memory_limit_gb": reported.get("memory_limit_gb"),
        "requested_cpu": agent.requested_cpu,
        "requested_memory_gb": agent.requested_memory_gb,
        "idle_timeout_s": agent.idle_timeout_s,
        "max_timeout_s": agent.requested_max_timeout_s,
        "created_at": agent.created_at,
        "provisioned_at": agent.provisioned_at,
        "terminated_at": agent.terminated_at,
        "last_ping_at": agent.last_ping_at,
        "last_active_at": agent.last_active_at,
        "snapshot_time": now,
    }


async def _upsert(target: AsyncConnection, table: sa.Table, rows: list[Row]) -> None:
    """Insert or update ``rows`` by ``table``'s primary key, writing only what changed."""
    key = [c.name for c in table.primary_key]
    # One statement cannot update the same row twice.
    unique = {tuple(r._mapping[k] for k in key): dict(r._mapping) for r in rows}
    if not unique:
        return
    stmt = pg_insert(table)
    columns = [c.name for c in table.columns if c.name not in key]
    await target.execute(
        stmt.on_conflict_do_update(
            index_elements=key,
            set_={c: stmt.excluded[c] for c in columns},
            where=sa.tuple_(*(table.c[c] for c in columns)).is_distinct_from(
                sa.tuple_(*(stmt.excluded[c] for c in columns))
            ),
        ),
        list(unique.values()),
    )


# ── Retention ─────────────────────────────────────────────────────────────────


async def _purge(target: AsyncConnection, now: datetime) -> None:
    """Delete what the copy keeps no longer: audit events as the source does, the
    rest by their own retention settings."""
    if days := settings.audit_retention_days:
        await target.execute(
            sa.delete(store.AUDIT).where(store.AUDIT.c.event_time < now - timedelta(days=days))
        )
    if days := settings.system_catalog_query_history_days:
        for table in (store.HISTORY, store.TABLE_ACCESS):
            await target.execute(
                sa.delete(table).where(table.c.finished_at < now - timedelta(days=days))
            )
    if days := settings.system_catalog_retention_days:
        kept = [(feed.table, feed.columns[0]) for feed in _FEEDS]
        for table, column in [*kept, (store.MAINTENANCE_OPERATIONS, "started_at")]:
            await target.execute(
                sa.delete(table).where(table.c[column] < now - timedelta(days=days))
            )


# ── Helpers ───────────────────────────────────────────────────────────────────


def _after(first: Any, second: Any, position: tuple[Any, Any]) -> sa.ColumnElement[bool]:
    """``(first, second)`` sorts after ``position``."""
    return sa.tuple_(first, second) > sa.tuple_(
        sa.literal(position[0], first.type), sa.literal(position[1], second.type)
    )


def _position(cursor: dict, txid: str, id_: str) -> tuple[int, uuid.UUID]:
    return int(cursor.get(txid, 0)), uuid.UUID(cursor.get(id_, str(_ZERO)))


def _older_than(at: str | None, now: datetime, age: timedelta) -> bool:
    return at is None or now - datetime.fromisoformat(at) >= age


def _due(state: dict, name: str, key: str, every: timedelta, now: datetime) -> bool:
    return _older_than(state.get(name, {}).get(key), now, every)
