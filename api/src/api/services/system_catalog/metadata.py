"""Copies each catalog's schemas, tables and columns into ``info_schema``.

Every ``SYSTEM_CATALOG_PUBLISH_INTERVAL_S`` one replica, whichever takes the
advisory lock, runs a tick:

1. **Marks what changed.** A finished statement that can change a catalog (a
   write, or DDL) marks the tables it names, or its whole catalog when it names a
   schema or cannot be read. So does a schema or table made or dropped through
   the REST API (:func:`mark_changed`).
2. **Re-reads what is marked, and what is due.** A catalog not read for
   ``SYSTEM_CATALOG_METADATA_REFRESH_S`` is read whole, a few per tick, oldest
   first; a marked table of an Iceberg catalog is re-read alone, and any mark on a
   DuckLake catalog, whose whole read costs three queries, re-reads it whole. Each
   catalog's rows are replaced in one transaction, so a query never sees half of
   one.

The statements a tick looks at are those finished since the last tick's newest,
so one committed late is missed until the catalog's next whole read.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import sqlalchemy as sa
import sqlglot
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession, async_sessionmaker
from sqlglot import exp

from api.config import settings
from api.models.catalog import Catalog, WorkspaceCatalog
from api.models.query import Query
from api.models.table_metadata import TableMetadata
from api.models.user import User
from api.services.catalog_backends import (
    CatalogBackendNotFound,
    CatalogTableInfo,
    backend_for,
)
from api.services.grants import target_tables
from api.services.polaris import PolarisClient
from api.services.system_catalog import store

logger = logging.getLogger(__name__)

# Distinct advisory-lock key in the "dhs" family ('dhsm': system catalog metadata).
_LOCK_KEY = 0x6468736D
# Whole reads one tick starts, so a deployment of many large Iceberg catalogs
# spreads them over several ticks.
WHOLE_READS_PER_TICK = 2
# Finished statements one tick reads for marks.
MARK_BATCH = 1000
# Statement kinds (`sql_classify`) that cannot change a catalog.
_READS = ("select", "describe")
_CURSOR = "metadata_marks"


@dataclass(frozen=True)
class Change:
    """Something a statement changed, as it named it. ``table`` None for the whole
    catalog (or schema); ``catalog`` and ``schema`` None for the current ones."""

    catalog: str | None
    schema: str | None
    table: str | None


def changes(sql: str) -> list[Change]:
    """What running ``sql`` may have changed in a catalog's schemas, tables and
    macros.

    Unreadable SQL changed the current catalog, for all this can tell; that
    includes a macro with a parameter default, which sqlglot cannot parse. A view
    changes nothing listed here.
    """
    try:
        statements = [s for s in sqlglot.parse(sql, read="duckdb") if s is not None]
    except Exception:  # noqa: BLE001 - sqlglot raises several kinds
        return [Change(None, None, None)]
    found: list[Change] = []
    for stmt in statements:
        if isinstance(stmt, exp.Create | exp.Drop):
            kind = str(stmt.args.get("kind") or "").upper()
            if kind == "SCHEMA":
                found += [Change(t.catalog or None, None, None) for t in target_tables(stmt)]
                continue
            if kind in ("MACRO", "FUNCTION"):
                # A macro lives in its schema, so its catalog is read again whole.
                if isinstance(stmt, exp.Create):
                    names = (
                        [stmt.this.this] if isinstance(stmt.this, exp.UserDefinedFunction) else []
                    )
                else:
                    names = stmt.args.get("tables") or []
                found += [
                    Change(n.catalog or None, n.db or None, None)
                    for n in names
                    if isinstance(n, exp.Table)
                ]
                continue
            if kind != "TABLE":
                continue
        tables = target_tables(stmt)
        if isinstance(stmt, exp.Copy) and stmt.args.get("kind") is True:
            tables = [stmt.this] if isinstance(stmt.this, exp.Table) else []
        for table in tables:
            found.append(Change(table.catalog or None, table.db or None, table.name or None))
            if isinstance(stmt, exp.Alter):
                # A rename's new name lives beside the old one.
                for action in stmt.args.get("actions") or []:
                    if isinstance(action, exp.AlterRename) and isinstance(action.this, exp.Table):
                        found.append(
                            Change(table.catalog or None, table.db or None, action.this.name)
                        )
    return found


@dataclass(frozen=True)
class _Attachments:
    by_name: dict[str, uuid.UUID]
    default: uuid.UUID | None


def resolve(
    change: Change,
    attachments: _Attachments,
    active_catalog: str | None,
    active_schema: str | None,
) -> list[tuple[uuid.UUID, str | None, str | None]]:
    """Which catalog's ``(schema, table)`` a change names, as marks.

    A two-part name may be a catalog's default schema or a schema of the current
    catalog, so it marks both. A table in a schema this cannot tell marks its
    whole catalog.
    """
    current = (
        attachments.by_name.get(active_catalog.lower()) if active_catalog else attachments.default
    )
    marks: list[tuple[uuid.UUID, str | None, str | None]] = []
    if change.catalog is not None:
        catalog = attachments.by_name.get(change.catalog.lower())
        if catalog is not None:
            table = change.table if change.schema is not None else None
            marks.append((catalog, change.schema, table))
        return marks
    if change.schema is not None and (named := attachments.by_name.get(change.schema.lower())):
        marks.append((named, None, None))
    if current is None:
        return marks
    schema = change.schema or active_schema
    if change.table is None or schema is None:
        marks.append((current, None, None))
    else:
        marks.append((current, schema, change.table))
    return marks


# ── Ticks ─────────────────────────────────────────────────────────────────────


async def refresh_once(
    session_factory: async_sessionmaker[AsyncSession],
    polaris: PolarisClient,
    *,
    now: datetime | None = None,
) -> bool:
    """Run one tick; return False without reading when another replica holds it."""
    now = now or datetime.now(UTC)
    async with store.get_engine().connect() as lock:
        if not await lock.scalar(sa.text("SELECT pg_try_advisory_lock(:k)"), {"k": _LOCK_KEY}):
            return False
        try:
            await _mark_from_queries(session_factory, now)
            await _read_marked_and_due(session_factory, polaris, now)
        finally:
            await lock.execute(sa.text("SELECT pg_advisory_unlock(:k)"), {"k": _LOCK_KEY})
            await lock.commit()
    return True


async def metadata_loop(
    session_factory: async_sessionmaker[AsyncSession], polaris: PolarisClient
) -> None:
    """Background loop: a tick each interval, once the store is usable."""
    interval = settings.system_catalog_publish_interval_s
    failing = False
    while True:
        try:
            if store.is_available():
                await refresh_once(session_factory, polaris)
                if failing:
                    logger.info("System catalog metadata refresh recovered")
                    failing = False
        except Exception:  # noqa: BLE001 - the loop must survive any tick failure
            if not failing:
                logger.exception(
                    "System catalog metadata refresh failed; retrying every %ss", interval
                )
            failing = True
        await asyncio.sleep(interval)


async def mark_changed(
    catalog_id: uuid.UUID, schema: str | None = None, table: str | None = None
) -> None:
    """Have the next tick re-read a table, or a whole catalog when ``table`` is None.

    For changes made other than by a statement. Never raises: a mark that cannot be
    recorded waits for the catalog's next whole read.
    """
    if not settings.system_catalog_enabled or not store.is_available():
        return
    try:
        async with store.get_engine().begin() as conn:
            await conn.execute(
                sa.insert(store.METADATA_CHANGES).values(
                    catalog_id=catalog_id,
                    schema_name=schema if table is not None else None,
                    table_name=table,
                )
            )
    except Exception:  # noqa: BLE001 - best effort, see above
        logger.warning("Could not mark catalog %s changed in the system catalog", catalog_id)


# ── Marks from finished statements ────────────────────────────────────────────


async def _mark_from_queries(session_factory: async_sessionmaker[AsyncSession], now: datetime):
    async with store.get_engine().begin() as conn:
        cursor = await conn.scalar(
            sa.select(store.CURSORS.c.value).where(store.CURSORS.c.name == _CURSOR)
        )
        if cursor is None:
            # Nothing to catch up on: every catalog is read whole first.
            await _save_cursor(conn, {"finished_at": now.isoformat(), "id": str(uuid.UUID(int=0))})
            return
        after = (datetime.fromisoformat(cursor["finished_at"]), uuid.UUID(cursor["id"]))
        async with session_factory() as source:
            rows = (
                await source.execute(
                    sa.select(
                        Query.id,
                        Query.finished_at,
                        Query.workspace_id,
                        Query.active_catalog,
                        Query.active_schema,
                        Query.sql,
                    )
                    .where(
                        # Beside the row comparison, so the finished_at index bounds the scan.
                        Query.finished_at >= after[0],
                        sa.tuple_(Query.finished_at, Query.id)
                        > sa.tuple_(
                            sa.literal(after[0], Query.finished_at.type),
                            sa.literal(after[1], Query.id.type),
                        ),
                        Query.status == "done",
                        sa.or_(Query.statement_type.is_(None), Query.statement_type.notin_(_READS)),
                    )
                    .order_by(Query.finished_at, Query.id)
                    .limit(MARK_BATCH)
                )
            ).all()
            attachments = await _attachments(source, {r.workspace_id for r in rows})
        marks = {
            mark
            for r in rows
            for change in changes(r.sql)
            for mark in resolve(
                change, attachments[r.workspace_id], r.active_catalog, r.active_schema
            )
        }
        if marks:
            await conn.execute(
                sa.insert(store.METADATA_CHANGES),
                [{"catalog_id": c, "schema_name": s, "table_name": t} for c, s, t in marks],
            )
        if rows:
            last = rows[-1]
            await _save_cursor(
                conn, {"finished_at": last.finished_at.isoformat(), "id": str(last.id)}
            )


async def _attachments(
    source: AsyncSession, workspace_ids: set[uuid.UUID]
) -> dict[uuid.UUID, _Attachments]:
    by_name: dict[uuid.UUID, dict[str, uuid.UUID]] = defaultdict(dict)
    default: dict[uuid.UUID, uuid.UUID] = {}
    if workspace_ids:
        for r in await source.execute(
            sa.select(
                WorkspaceCatalog.workspace_id, WorkspaceCatalog.is_default, Catalog.id, Catalog.slug
            )
            .join(Catalog, Catalog.id == WorkspaceCatalog.catalog_id)
            .where(WorkspaceCatalog.workspace_id.in_(workspace_ids))
        ):
            by_name[r.workspace_id][r.slug.lower()] = r.id
            if r.is_default:
                default[r.workspace_id] = r.id
    return {ws: _Attachments(by_name[ws], default.get(ws)) for ws in workspace_ids}


async def _save_cursor(conn: AsyncConnection, value: dict[str, str]) -> None:
    stmt = pg_insert(store.CURSORS).values(name=_CURSOR, value=value)
    await conn.execute(
        stmt.on_conflict_do_update(
            index_elements=["name"],
            set_={"value": stmt.excluded["value"], "updated_at": sa.func.now()},
        )
    )


# ── Reading catalogs ──────────────────────────────────────────────────────────

# Catalogs whose last read failed, so a catalog that stays unreachable logs once.
_failing: set[uuid.UUID] = set()


async def _read_marked_and_due(
    session_factory: async_sessionmaker[AsyncSession], polaris: PolarisClient, now: datetime
) -> None:
    async with session_factory() as source:
        catalogs = list(await source.scalars(sa.select(Catalog)))
    ids = [c.id for c in catalogs]
    async with store.get_engine().begin() as conn:
        # A deleted catalog's rows go with it.
        for table in (*_PER_CATALOG, store.METADATA_READS, store.METADATA_CHANGES):
            await conn.execute(sa.delete(table).where(table.c.catalog_id.notin_(ids)))
        read_at = dict(
            (
                await conn.execute(
                    sa.select(store.METADATA_READS.c.catalog_id, store.METADATA_READS.c.read_at)
                )
            ).all()
        )
        marked: dict[uuid.UUID, list[Any]] = defaultdict(list)
        for r in await conn.execute(sa.select(store.METADATA_CHANGES)):
            marked[r.catalog_id].append(r)

    every = timedelta(seconds=settings.system_catalog_metadata_refresh_s)
    epoch = datetime.min.replace(tzinfo=UTC)
    due = sorted(
        (c for c in catalogs if now - read_at.get(c.id, epoch) >= every),
        key=lambda c: read_at.get(c.id, epoch),
    )[:WHOLE_READS_PER_TICK]
    whole = {c.id for c in due}
    for catalog in catalogs:
        changes_ = marked.get(catalog.id, [])
        if catalog.id not in whole and not changes_:
            continue
        tables = {(r.schema_name, r.table_name) for r in changes_}
        try:
            if (
                catalog.id in whole
                or catalog.kind != "iceberg_polaris"
                or any(t is None for _, t in tables)
            ):
                await _read_whole(session_factory, polaris, catalog, now)
            else:
                await _read_tables(session_factory, polaris, catalog, tables, now)
        except Exception as exc:  # noqa: BLE001 - one catalog must not stop the others
            if catalog.id not in _failing:
                logger.warning(
                    "Could not read catalog %s for the system catalog: %s", catalog.slug, exc
                )
            _failing.add(catalog.id)
            if catalog.id in whole:
                # Retried when due again, so it does not take every tick's turn;
                # its marks stay and are retried each tick.
                await _record_read(catalog.id, now)
            continue
        _failing.discard(catalog.id)
        if changes_:
            async with store.get_engine().begin() as conn:
                await conn.execute(
                    sa.delete(store.METADATA_CHANGES).where(
                        store.METADATA_CHANGES.c.id.in_([r.id for r in changes_])
                    )
                )


_PER_CATALOG = (store.INFO_SCHEMATA, store.INFO_TABLES, store.INFO_COLUMNS, store.INFO_ROUTINES)


async def _read_whole(
    session_factory: async_sessionmaker[AsyncSession],
    polaris: PolarisClient,
    catalog: Catalog,
    now: datetime,
) -> None:
    description = await backend_for(catalog, polaris=polaris).describe_catalog(catalog)
    tracked = await _tracked(session_factory, catalog.id)
    async with store.get_engine().begin() as conn:
        for table in _PER_CATALOG:
            await conn.execute(sa.delete(table).where(table.c.catalog_id == catalog.id))
        schemas = {s.name for s in description.schemas} | {
            t.schema_name for t in description.tables
        }
        if schemas:
            await conn.execute(
                sa.insert(store.INFO_SCHEMATA),
                [
                    {
                        "catalog_id": catalog.id,
                        "catalog_name": catalog.slug,
                        "schema_name": s,
                        "refreshed_at": now,
                    }
                    for s in sorted(schemas)
                ],
            )
        await _insert_tables(conn, catalog, description.tables, tracked, now)
        if description.routines:
            await conn.execute(
                sa.insert(store.INFO_ROUTINES),
                [
                    {
                        "catalog_id": catalog.id,
                        "catalog_name": catalog.slug,
                        "schema_name": r.schema_name,
                        "routine_name": r.name,
                        "overload": r.overload,
                        "routine_type": r.routine_type,
                        "parameters": ", ".join(r.parameters),
                        "definition": r.definition,
                        "refreshed_at": now,
                    }
                    for r in description.routines
                ],
            )
        await _record_read(catalog.id, now, conn)


async def _record_read(
    catalog_id: uuid.UUID, now: datetime, conn: AsyncConnection | None = None
) -> None:
    if conn is None:
        async with store.get_engine().begin() as own:
            return await _record_read(catalog_id, now, own)
    stmt = pg_insert(store.METADATA_READS).values(catalog_id=catalog_id, read_at=now)
    await conn.execute(
        stmt.on_conflict_do_update(index_elements=["catalog_id"], set_={"read_at": now})
    )


async def _read_tables(
    session_factory: async_sessionmaker[AsyncSession],
    polaris: PolarisClient,
    catalog: Catalog,
    tables: set[tuple[str | None, str | None]],
    now: datetime,
) -> None:
    """Re-read the marked tables alone. A mark names a table as a statement spelled
    it, so it is matched to the stored spelling first; one that matches nothing
    and cannot be read either (a table of another name, or a typo that failed)
    has the whole catalog re-read."""
    backend = backend_for(catalog, polaris=polaris)
    async with store.get_engine().connect() as conn:
        stored = {
            (r.schema_name.lower(), r.table_name.lower()): (r.schema_name, r.table_name)
            for r in await conn.execute(
                sa.select(store.INFO_TABLES.c.schema_name, store.INFO_TABLES.c.table_name).where(
                    store.INFO_TABLES.c.catalog_id == catalog.id
                )
            )
        }
    read: dict[tuple[str, str], CatalogTableInfo | None] = {}
    for schema, table in tables:
        if schema is None or table is None:
            continue
        spelled = stored.get((schema.lower(), table.lower()), (schema, table))
        try:
            read[spelled] = await backend.get_table(catalog, *spelled)
        except CatalogBackendNotFound:
            if spelled not in stored.values():
                await _read_whole(session_factory, polaris, catalog, now)
                return
            read[spelled] = None
    tracked = await _tracked(session_factory, catalog.id)
    async with store.get_engine().begin() as conn:
        for schema, table in read:
            for t in (store.INFO_TABLES, store.INFO_COLUMNS):
                await conn.execute(
                    sa.delete(t).where(
                        t.c.catalog_id == catalog.id,
                        t.c.schema_name == schema,
                        t.c.table_name == table,
                    )
                )
        found = [info for info in read.values() if info is not None]
        for schema in {t.schema_name for t in found}:
            await conn.execute(
                pg_insert(store.INFO_SCHEMATA)
                .values(
                    catalog_id=catalog.id,
                    catalog_name=catalog.slug,
                    schema_name=schema,
                    refreshed_at=now,
                )
                .on_conflict_do_nothing()
            )
        await _insert_tables(conn, catalog, found, tracked, now)


@dataclass(frozen=True)
class _Tracked:
    row_count: int | None
    size_bytes: int | None
    owner: str | None
    last_write_at: datetime | None
    snapshot_at: datetime | None


async def _tracked(
    session_factory: async_sessionmaker[AsyncSession], catalog_id: uuid.UUID
) -> dict[tuple[str, str], _Tracked]:
    """What DuckHaven records of each table beside the catalog (`table_metadata`)."""
    async with session_factory() as source:
        rows = await source.execute(
            sa.select(
                TableMetadata.schema_name,
                TableMetadata.table_name,
                TableMetadata.row_count,
                TableMetadata.size_bytes,
                TableMetadata.last_write_at,
                TableMetadata.snapshot_at,
                User.email,
            )
            .outerjoin(User, User.id == TableMetadata.owner_id)
            .where(TableMetadata.catalog_id == catalog_id)
        )
        return {
            (r.schema_name, r.table_name): _Tracked(
                r.row_count, r.size_bytes, r.email, r.last_write_at, r.snapshot_at
            )
            for r in rows
        }


def _last_altered(table: CatalogTableInfo, meta: _Tracked | None) -> datetime | None:
    """The latest change anyone recorded: the catalog's own record (Iceberg's), the
    snapshot DuckHaven last saw after a write, or its creation on the Catalog page."""
    known = [table.last_modified, *((meta.snapshot_at, meta.last_write_at) if meta else ())]
    return max((t for t in known if t is not None), default=None)


def _bytes(table: CatalogTableInfo, meta: _Tracked | None) -> int | None:
    if table.size_bytes is not None:
        return table.size_bytes
    if (from_snapshot := _summary_metric(table, "total-files-size")) is not None:
        return from_snapshot
    return meta.size_bytes if meta else None


def _summary_metric(table: CatalogTableInfo, key: str) -> int | None:
    try:
        return int((table.current_snapshot_summary or {})[key])
    except KeyError, TypeError, ValueError:
        return None


async def _insert_tables(
    conn: AsyncConnection,
    catalog: Catalog,
    tables: list[CatalogTableInfo],
    tracked: dict[tuple[str, str], _Tracked],
    now: datetime,
) -> None:
    """The rows of info_schema.tables and .columns for ``tables``, with the figures
    a table's page shows: the catalog's own where it has them, else DuckHaven's."""
    table_rows, column_rows = [], []
    for t in tables:
        meta = tracked.get((t.schema_name, t.name))
        key = {"catalog_id": catalog.id, "catalog_name": catalog.slug, "schema_name": t.schema_name}
        table_rows.append(
            {
                **key,
                "table_name": t.name,
                "table_type": "BASE TABLE",
                "table_format": t.data_source_format,
                "column_count": len(t.columns),
                "row_count": (
                    meta.row_count
                    if meta and meta.row_count is not None
                    else _summary_metric(t, "total-records")
                ),
                "bytes": _bytes(t, meta),
                "table_owner": meta.owner if meta else None,
                "last_altered": _last_altered(t, meta),
                "refreshed_at": now,
            }
        )
        column_rows += [
            {
                **key,
                "table_name": t.name,
                "column_name": c.name,
                "ordinal_position": c.position + 1,
                "data_type": c.duckdb_type or c.type_name,
                "source_type": c.type_text,
                "is_nullable": "YES" if c.nullable else "NO",
            }
            for c in t.columns
        ]
    if table_rows:
        await conn.execute(sa.insert(store.INFO_TABLES), table_rows)
    if column_rows:
        await conn.execute(sa.insert(store.INFO_COLUMNS), column_rows)
