"""The system-catalog materializer.

A periodic control-plane job that copies DuckHaven's own state into the Iceberg
``duckhaven`` catalog so it is SQL-queryable through the normal agent path:

- ``query.history`` / ``access.audit`` — incrementally appended from terminal
  ``queries`` rows (Postgres stays the single source of truth; this is a
  bounded-latency derived copy, like Snowflake ``ACCOUNT_USAGE``).
- ``info_schema.{catalogs,schemas,tables,columns}`` — a current-state snapshot
  rebuilt each cycle from Polaris + the ``table_metadata`` sidecar.

The first run (null cursor) backfills all existing history.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

import duckdb
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from api.config import settings
from api.models.agent import Agent
from api.models.catalog import Catalog
from api.models.query import Query
from api.models.system_catalog import SystemCatalogSync
from api.models.table_metadata import TableMetadata
from api.models.user import User
from api.models.workspace import Workspace
from api.services.polaris import PolarisClient, PolarisError
from api.services.system_catalog import tables
from api.services.system_catalog.writer import SystemCatalogWriter

logger = logging.getLogger(__name__)

# Cap rows copied per cycle so a large backlog (first-run backfill) is drained
# over a few cycles instead of one huge write.
_BATCH_SIZE = 1000


def statement_type(sql: str) -> str:
    """The first statement's type (SELECT/INSERT/…), or UNKNOWN if unparseable."""
    try:
        statements = duckdb.extract_statements(sql)
    except Exception:  # noqa: BLE001 - audit metadata is best-effort
        return "UNKNOWN"
    if not statements:
        return "UNKNOWN"
    return statements[0].type.name


def _profile_int(profile: Any, key: str) -> int | None:
    if isinstance(profile, dict):
        summary = profile.get("summary")
        if isinstance(summary, dict) and isinstance(summary.get(key), int):
            return summary[key]
    return None


def _history_row(query: Query, slug: str, agent_name: str | None, email: str | None) -> dict:
    return {
        "query_id": str(query.id),
        "workspace_id": str(query.workspace_id),
        "workspace_slug": slug,
        "agent_id": str(query.agent_id) if query.agent_id else None,
        "agent_name": agent_name,
        "user_id": str(query.user_id) if query.user_id else None,
        "user_email": email,
        "statement_type": statement_type(query.sql),
        "status": query.status,
        "origin": query.origin,
        "row_count": query.row_count,
        "result_bytes": query.result_bytes,
        "duration_ms": query.duration_ms,
        "reserved_memory_bytes": _profile_int(query.profile, "reserved_memory_bytes"),
        "reserved_threads": _profile_int(query.profile, "reserved_threads"),
        "error": query.error,
        "started_at": query.started_at,
        "finished_at": query.finished_at,
    }


def _audit_row(history: dict) -> dict:
    return {
        "event_time": history["finished_at"] or history["started_at"],
        "query_id": history["query_id"],
        "actor": history["user_email"],
        "action": history["statement_type"],
        "workspace_slug": history["workspace_slug"],
        "status": history["status"],
    }


async def _get_sync(db: AsyncSession) -> SystemCatalogSync:
    row = await db.get(SystemCatalogSync, 1)
    if row is None:
        row = SystemCatalogSync(id=1)
        db.add(row)
        await db.flush()
    return row


async def materialize_query_history(
    db: AsyncSession, writer: SystemCatalogWriter, *, batch_size: int = _BATCH_SIZE
) -> int:
    """Append terminal queries newer than the cursor into history + audit.

    Returns the number of rows copied. Idempotent: the strict ``(started_at, id)``
    cursor guarantees each query is copied once.
    """
    sync = await _get_sync(db)
    stmt = (
        sa.select(Query, Workspace.slug, Agent.name, User.email)
        .join(Workspace, Workspace.id == Query.workspace_id)
        .outerjoin(Agent, Agent.id == Query.agent_id)
        .outerjoin(User, User.id == Query.user_id)
        .where(Query.finished_at.isnot(None))
        .order_by(Query.started_at, Query.id)
        .limit(batch_size)
    )
    if sync.query_cursor_started_at is not None:
        cur_ts, cur_id = sync.query_cursor_started_at, sync.query_cursor_id
        stmt = stmt.where(
            sa.or_(
                Query.started_at > cur_ts,
                sa.and_(Query.started_at == cur_ts, Query.id > cur_id),
            )
        )

    rows = (await db.execute(stmt)).all()
    if not rows:
        return 0

    history = [_history_row(q, slug, agent, email) for (q, slug, agent, email) in rows]
    writer.append(tables.QUERY_HISTORY, history)
    writer.append(tables.ACCESS_AUDIT, [_audit_row(h) for h in history])

    last_query = rows[-1][0]
    sync.query_cursor_started_at = last_query.started_at
    sync.query_cursor_id = last_query.id
    await db.flush()
    return len(history)


async def materialize_info_schema(
    db: AsyncSession, polaris: PolarisClient, writer: SystemCatalogWriter
) -> None:
    """Rebuild the current-state object-metadata snapshot from Polaris + sidecar."""
    catalogs = (
        (
            await db.execute(
                sa.select(Catalog)
                .options(selectinload(Catalog.storage_backend))
                .order_by(Catalog.slug)
            )
        )
        .scalars()
        .all()
    )
    metadata = {
        (m.catalog_id, m.schema_name, m.table_name): m
        for m in (await db.execute(sa.select(TableMetadata))).scalars().all()
    }
    emails = dict(
        (uid, email) for uid, email in (await db.execute(sa.select(User.id, User.email))).all()
    )

    catalog_rows: list[dict] = []
    schema_rows: list[dict] = []
    table_rows: list[dict] = []
    column_rows: list[dict] = []

    for catalog in catalogs:
        catalog_rows.append(
            {
                "catalog": catalog.slug,
                "polaris_name": catalog.polaris_name,
                "storage_kind": catalog.storage_backend.kind if catalog.storage_backend else None,
                "is_system": catalog.is_system,
                "created_at": catalog.created_at,
            }
        )
        try:
            schemas = await polaris.list_schemas(catalog.polaris_name)
        except PolarisError as exc:
            logger.warning("info_schema: list_schemas failed for %s: %s", catalog.slug, exc)
            continue
        for schema in schemas:
            schema_rows.append({"catalog": catalog.slug, "schema_name": schema.name})
            try:
                polaris_tables = await polaris.list_tables(catalog.polaris_name, schema.name)
            except PolarisError as exc:
                logger.warning("info_schema: list_tables failed for %s: %s", catalog.slug, exc)
                continue
            for tbl in polaris_tables:
                meta = metadata.get((catalog.id, schema.name, tbl.name))
                table_rows.append(
                    {
                        "catalog": catalog.slug,
                        "schema_name": schema.name,
                        "table_name": tbl.name,
                        "owner_email": emails.get(meta.owner_id) if meta else None,
                        "row_count": meta.row_count if meta else None,
                        "size_bytes": meta.size_bytes if meta else None,
                        "last_write_at": meta.last_write_at if meta else None,
                    }
                )
                # Column detail needs a per-table loadTable (best-effort: a load
                # failure drops only that table's columns).
                try:
                    detail = await polaris.get_table(catalog.polaris_name, schema.name, tbl.name)
                except PolarisError as exc:
                    logger.warning("info_schema: get_table failed for %s: %s", tbl.name, exc)
                    continue
                for col in detail.columns:
                    column_rows.append(
                        {
                            "catalog": catalog.slug,
                            "schema_name": schema.name,
                            "table_name": tbl.name,
                            "column_name": col.name,
                            "data_type": col.type_text,
                            "ordinal": col.position,
                        }
                    )

    writer.overwrite(tables.INFO_CATALOGS, catalog_rows)
    writer.overwrite(tables.INFO_SCHEMAS, schema_rows)
    writer.overwrite(tables.INFO_TABLES, table_rows)
    writer.overwrite(tables.INFO_COLUMNS, column_rows)


async def run_cycle(
    session_factory: async_sessionmaker[AsyncSession],
    polaris: PolarisClient,
    writer: SystemCatalogWriter,
) -> dict[str, Any]:
    """Run one materialization cycle (history + info_schema), recording status."""
    async with session_factory() as db:
        sync = await _get_sync(db)
        try:
            copied = await materialize_query_history(db, writer)
            await materialize_info_schema(db, polaris, writer)
            sync.last_run_at = datetime.now(tz=UTC)
            sync.last_error = None
            await db.commit()
            return {"status": "ran", "copied": copied}
        except Exception as exc:  # noqa: BLE001 - persist the error, keep looping
            await db.rollback()
            sync = await _get_sync(db)
            sync.last_run_at = datetime.now(tz=UTC)
            sync.last_error = str(exc)[:2048]
            await db.commit()
            raise


async def materializer_loop(
    session_factory: async_sessionmaker[AsyncSession],
    polaris: PolarisClient,
    writer: SystemCatalogWriter,
) -> None:
    """Background loop: copy on a fixed interval. One bad cycle never kills it."""
    interval = settings.system_catalog_sync_interval_s
    logger.info("System catalog materializer started (interval %.0fs)", interval)
    while True:
        try:
            result = await run_cycle(session_factory, polaris, writer)
            if result.get("copied"):
                logger.info("System catalog materializer: %s", result)
        except Exception as exc:  # noqa: BLE001 - the loop must survive any cycle
            logger.exception("System catalog materializer cycle failed: %s", exc)
        await asyncio.sleep(interval)
