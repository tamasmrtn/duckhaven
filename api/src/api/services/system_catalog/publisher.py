"""Copies the audit log, current access and query history into the system catalog store.

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
  updated in place.
- **access.grants** is replaced whole, after a tick copies an access or identity
  event and at least every 5 minutes, which covers what changes access without an
  event (deleting an agent takes its grants with it). So are
  ``info_schema.catalogs`` and the ``_acl`` tables the row filters consult, from
  the same read.

A tick copies at most ``BATCH`` rows of each kind; while either is behind, the
next tick starts at once instead of after the interval.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Row
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession, async_sessionmaker
from sqlalchemy.orm import aliased

from api.config import settings
from api.models.agent import Agent
from api.models.agent_grant import AgentGrant
from api.models.audit import AuditEvent
from api.models.catalog import Catalog, WorkspaceCatalog
from api.models.catalog_grant import CatalogGrant
from api.models.query import Query
from api.models.user import SERVICE_ACCOUNT_PROVIDER, User
from api.models.workspace import Workspace, WorkspaceMember
from api.services.query_history import HIDDEN_ORIGINS
from api.services.system_catalog import store

logger = logging.getLogger(__name__)

# Distinct advisory-lock key in the "dhs" family ('dhsy': system catalog publish).
_TICK_LOCK_KEY = 0x64687379
BATCH = 5000
GRANTS_REFRESH = timedelta(minutes=5)
HISTORY_SWEEP_EVERY = timedelta(minutes=1)
HISTORY_LATE_WINDOW = timedelta(minutes=15)
PURGE_EVERY = timedelta(hours=1)
TERMINAL_STATUSES = ("done", "failed", "cancelled")
# Changing either can change who reaches what.
_ACCESS_CATEGORIES = frozenset({"access", "identity"})
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
        if access_changed or _due(state, "grants", "synced_at", GRANTS_REFRESH, now):
            await _refresh_grants(session_factory, target, now)
            state["grants"] = {"synced_at": now.isoformat()}
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
    return audit_more or history_more


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
    days = settings.system_catalog_query_history_days
    floor = now - timedelta(days=days) if days else None
    after = (
        (datetime.fromisoformat(cursor["finished_at"]), uuid.UUID(cursor["id"]))
        if cursor.get("finished_at")
        else None
    )
    rows = await history_rows(source, after=after, floor=floor)
    await _upsert_history(target, rows)
    if rows:
        after = (rows[-1].finished_at, rows[-1].id)
    more = len(rows) == BATCH

    swept_at = cursor.get("swept_at")
    if not more and after and _older_than(swept_at, now, HISTORY_SWEEP_EVERY):
        page_after = (after[0] - HISTORY_LATE_WINDOW, _ZERO)
        while page := await history_rows(source, after=page_after, through=after, floor=floor):
            await _upsert_history(target, page)
            if len(page) < BATCH:
                break
            page_after = (page[-1].finished_at, page[-1].id)
        swept_at = now.isoformat()

    state["history"] = {
        "finished_at": after[0].isoformat() if after else None,
        "id": str(after[1]) if after else None,
        "swept_at": swept_at,
        # Caught up means every run that had finished when the tick began.
        "synced_at": cursor.get("synced_at") if more else now.isoformat(),
    }
    return more


async def _upsert_history(target: AsyncConnection, rows: list[Row]) -> None:
    if not rows:
        return
    stmt = pg_insert(store.HISTORY)
    columns = [c.name for c in store.HISTORY.columns if c.name != "id"]
    await target.execute(
        stmt.on_conflict_do_update(
            index_elements=["id"],
            set_={c: stmt.excluded[c] for c in columns},
            # A re-read of an unchanged run writes nothing.
            where=sa.tuple_(*(store.HISTORY.c[c] for c in columns)).is_distinct_from(
                sa.tuple_(*(stmt.excluded[c] for c in columns))
            ),
        ),
        [dict(r._mapping) for r in rows],
    )


# ── access.grants ─────────────────────────────────────────────────────────────

_GRANT_COLUMNS = [c.name for c in store.GRANTS.columns]


def _principal(user: Any) -> dict[str, Any]:
    return {
        "principal_type": (
            "service_account" if user.auth_provider == SERVICE_ACCOUNT_PROVIDER else "user"
        ),
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


async def _refresh_grants(
    session_factory: async_sessionmaker[AsyncSession], target: AsyncConnection, now: datetime
) -> None:
    """Replace access.grants, and what info_schema's row filters consult, with a
    snapshot read in one consistent view."""
    async with session_factory() as snapshot:
        await snapshot.connection(execution_options={"isolation_level": "REPEATABLE READ"})
        tables = {store.GRANTS: await grant_rows(snapshot, now), **await visibility_rows(snapshot)}
    for table, rows in tables.items():
        # Not TRUNCATE: that would block readers until the tick commits.
        await target.execute(sa.delete(table))
        if rows:
            await target.execute(sa.insert(table), rows)


# ── Retention ─────────────────────────────────────────────────────────────────


async def _purge(target: AsyncConnection, now: datetime) -> None:
    """Delete what the copy keeps no longer: audit events as the source does."""
    if days := settings.audit_retention_days:
        await target.execute(
            sa.delete(store.AUDIT).where(store.AUDIT.c.event_time < now - timedelta(days=days))
        )
    if days := settings.system_catalog_query_history_days:
        await target.execute(
            sa.delete(store.HISTORY).where(store.HISTORY.c.finished_at < now - timedelta(days=days))
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
