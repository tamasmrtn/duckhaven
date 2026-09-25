"""Migrating a DuckLake catalog between storage backends.

File paths nest relative to a single ``data_path`` in ``ducklake_metadata``, so
moving the catalog is a prefix copy plus one row update: no shadow catalog and
no metadata rewrite. Phases, statuses and checkpoints are the Iceberg path's;
only the phase bodies differ. Checkpoints are per table, each one a prefix.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime
from urllib.parse import urlparse

import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.catalog import Catalog, WorkspaceCatalog
from api.models.catalog_migration import CatalogMigration, CatalogMigrationTable
from api.models.query import Query
from api.models.storage_backend import StorageBackend
from api.services.migration import (
    STATUS_COPYING,
    STATUS_CUTOVER,
    STATUS_VERIFYING,
    TABLE_PENDING,
    TABLE_REGISTERED,
    TABLE_VERIFIED,
)
from api.services.migration.storage_io import (
    StorageContext,
    context_from_duckdb_block,
    copy_object,
    list_objects,
)

logger = logging.getLogger(__name__)

# How long to wait for statements already running at freeze time to drain;
# their writes would otherwise land under the source prefix mid-copy.
QUIESCE_TIMEOUT_S = 300.0


async def absolute_path_count(catalog: Catalog) -> int:
    """Live data and delete files recorded with an absolute path.

    These (from `ducklake_add_data_files`) do not move with ``data_path``, so a
    migration refuses them rather than rewriting them on a guess.
    """
    from api.services.catalog_backends.ducklake import get_engine

    schema = catalog.metadata_schema
    if not schema:
        return 0
    sql = text(
        f'SELECT (SELECT count(*) FROM "{schema}".ducklake_data_file '
        "         WHERE end_snapshot IS NULL AND path_is_relative = false) "
        f'     + (SELECT count(*) FROM "{schema}".ducklake_delete_file '
        "         WHERE end_snapshot IS NULL AND path_is_relative = false)"
    )
    try:
        async with get_engine().connect() as conn:
            return int((await conn.execute(sql)).scalar_one())
    except Exception:  # noqa: BLE001 - a catalog never attached has no tables yet
        return 0


def _context(backend: StorageBackend, data_path: str) -> StorageContext:
    from api.services.session_credentials import build_storage_block

    return context_from_duckdb_block(
        backend.kind, build_storage_block(backend, data_path), backend.config
    )


def _data_path_for(backend: StorageBackend, slug: str) -> str:
    """Where this catalog's data would live on ``backend``."""
    from api.services.workspace import polaris_storage

    _, base, _ = polaris_storage(backend.kind, backend.root_uri or "", backend.config)
    return f"{base.rstrip('/')}/{slug}/"


async def in_flight_statements(db: AsyncSession, catalog_id: uuid.UUID) -> int:
    """Statements still running in any workspace this catalog is attached to."""
    return int(
        await db.scalar(
            sa.select(sa.func.count())
            .select_from(Query)
            .join(WorkspaceCatalog, WorkspaceCatalog.workspace_id == Query.workspace_id)
            .where(
                WorkspaceCatalog.catalog_id == catalog_id,
                Query.status.in_(("queued", "running")),
            )
        )
        or 0
    )


async def set_agent_dml(catalog: Catalog, *, allowed: bool) -> None:
    """Grant or revoke the catalog role's write access to its metadata schema.

    Backs the submit-time freeze with a PostgreSQL-enforced one.
    """
    from api.services.catalog_backends.ducklake import agent_role_for, get_engine

    schema, role = catalog.metadata_schema, agent_role_for(catalog.slug)
    if not schema:
        return
    verb = "GRANT" if allowed else "REVOKE"
    direction = "TO" if allowed else "FROM"
    async with get_engine().begin() as conn:
        await conn.execute(
            text(
                f'{verb} INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA "{schema}" '
                f'{direction} "{role}"'
            )
        )


async def provision(db: AsyncSession, migration: CatalogMigration, log) -> None:  # noqa: ANN001
    """Record the two paths, wait for writes to drain, enumerate the tables."""
    catalog = await db.get(Catalog, migration.catalog_id)
    # By id: the lazy `catalog.storage_backend` raises MissingGreenlet here.
    source = await db.get(StorageBackend, migration.source_storage_backend_id)
    target = await db.get(StorageBackend, migration.target_storage_backend_id)
    assert catalog and source and target

    remaining = await absolute_path_count(catalog)
    if remaining:
        raise RuntimeError(
            f"{remaining} data file(s) are recorded with an absolute path and will not "
            "move with the catalog. Rewrite or drop them before migrating."
        )

    if migration.started_at is None:
        migration.started_at = datetime.now(tz=UTC)
        migration.source_data_path = _data_path_for(source, catalog.slug)
        migration.target_data_path = _data_path_for(target, catalog.slug)
        await db.commit()

    # Stay in `pending` while anything is running; the runner retries next tick.
    busy = await in_flight_statements(db, catalog.id)
    if busy:
        elapsed = (datetime.now(tz=UTC) - migration.started_at).total_seconds()
        if elapsed > QUIESCE_TIMEOUT_S:
            raise RuntimeError(
                f"{busy} statement(s) were still running after {int(elapsed)}s; "
                "the catalog never went quiet enough to copy safely."
            )
        await log(db, migration.id, "info", f"Waiting for {busy} in-flight statement(s)")
        await db.commit()
        return

    await set_agent_dml(catalog, allowed=False)
    await log(db, migration.id, "info", "Writes revoked on the catalog's metadata schema")

    tables = await _enumerate(catalog)
    for schema_name, table_name in tables:
        db.add(
            CatalogMigrationTable(
                migration_id=migration.id,
                schema_name=schema_name,
                table_name=table_name,
                status=TABLE_PENDING,
            )
        )
    migration.tables_total = len(tables)
    migration.status = STATUS_COPYING
    await log(db, migration.id, "info", f"Copying {len(tables)} table(s)")
    await db.commit()


async def _enumerate(catalog: Catalog) -> list[tuple[str, str]]:
    from api.services.catalog_backends.ducklake import DuckLakeCatalogBackend

    backend = DuckLakeCatalogBackend()
    out: list[tuple[str, str]] = []
    for schema in await backend.list_schemas(catalog):
        out.extend((schema.name, t.name) for t in await backend.list_tables(catalog, schema.name))
    return out


async def copy(db: AsyncSession, migration: CatalogMigration, log) -> None:  # noqa: ANN001
    """Copy each table's prefix, then sweep anything not attributed to a table."""
    catalog = await db.get(Catalog, migration.catalog_id)
    source = await db.get(StorageBackend, migration.source_storage_backend_id)
    target = await db.get(StorageBackend, migration.target_storage_backend_id)
    assert catalog and source and target

    src_path, dst_path = migration.source_data_path, migration.target_data_path
    assert src_path and dst_path
    src = _context(source, src_path)
    dst = _context(target, dst_path)

    rows = (
        (
            await db.execute(
                sa.select(CatalogMigrationTable)
                .where(
                    CatalogMigrationTable.migration_id == migration.id,
                    CatalogMigrationTable.status.notin_((TABLE_REGISTERED, TABLE_VERIFIED)),
                )
                .order_by(CatalogMigrationTable.schema_name, CatalogMigrationTable.table_name)
            )
        )
        .scalars()
        .all()
    )

    for row in rows:
        if migration.cancel_requested:
            return
        prefix = await _table_prefix(catalog, row.schema_name, row.table_name)
        copied = await asyncio.to_thread(
            _copy_prefix, src, dst, f"{src_path}{prefix}", f"{dst_path}{prefix}"
        )
        row.bytes_copied = copied
        row.status = TABLE_REGISTERED
        migration.tables_done += 1
        migration.bytes_copied += copied
        await log(
            db, migration.id, "info", f"Copied {row.schema_name}.{row.table_name} ({copied} bytes)"
        )
        await db.commit()

    # Sweep catalog-level files that belong to no table.
    swept = await asyncio.to_thread(_copy_prefix, src, dst, src_path, dst_path)
    if swept:
        migration.bytes_copied += swept
        await log(db, migration.id, "info", f"Copied {swept} bytes not attributed to a table")

    migration.status = STATUS_VERIFYING
    await db.commit()


async def _table_prefix(catalog: Catalog, schema_name: str, table_name: str) -> str:
    """The table's location relative to the catalog's data path."""
    from api.services.catalog_backends.ducklake import get_engine

    sql = text(
        f'SELECT s.path, t.path FROM "{catalog.metadata_schema}".ducklake_table t '
        f'JOIN "{catalog.metadata_schema}".ducklake_schema s ON s.schema_id = t.schema_id '
        "WHERE t.end_snapshot IS NULL AND s.end_snapshot IS NULL "
        "AND s.schema_name = :schema AND t.table_name = :table"
    )
    async with get_engine().connect() as conn:
        row = (await conn.execute(sql, {"schema": schema_name, "table": table_name})).first()
    if row is None:
        return ""
    return f"{row[0] or ''}{row[1] or ''}"


def _copy_prefix(src: StorageContext, dst: StorageContext, src_prefix: str, dst_prefix: str) -> int:
    """Copy every object under a prefix, skipping ones already the right size."""
    from api.services.migration.storage_io import object_size

    copied = 0
    for uri, size in list_objects(src, src_prefix):
        target_uri = dst_prefix + uri[len(src_prefix) :] if uri.startswith(src_prefix) else None
        if target_uri is None:
            continue
        if object_size(dst, target_uri) == size:
            continue
        copied += copy_object(src, dst, uri, target_uri)
    return copied


async def verify(db: AsyncSession, migration: CatalogMigration, log) -> None:  # noqa: ANN001
    """Every source object exists at the target with the same size."""
    catalog = await db.get(Catalog, migration.catalog_id)
    source = await db.get(StorageBackend, migration.source_storage_backend_id)
    target = await db.get(StorageBackend, migration.target_storage_backend_id)
    assert catalog and source and target

    src_path, dst_path = migration.source_data_path, migration.target_data_path
    assert src_path and dst_path
    src = _context(source, src_path)
    dst = _context(target, dst_path)

    missing = await asyncio.to_thread(_missing_at_target, src, dst, src_path, dst_path)
    if missing:
        raise RuntimeError(
            f"{len(missing)} object(s) are missing or the wrong size at the target, "
            f"starting with {missing[0]}"
        )

    # Re-checked: a write that slipped the freeze could have added one mid-copy.
    remaining = await absolute_path_count(catalog)
    if remaining:
        raise RuntimeError(
            f"{remaining} absolute-path file(s) appeared during the copy; the catalog "
            "was written to while migrating."
        )

    await db.execute(
        sa.update(CatalogMigrationTable)
        .where(CatalogMigrationTable.migration_id == migration.id)
        .values(status=TABLE_VERIFIED)
    )
    migration.status = STATUS_CUTOVER
    await log(db, migration.id, "info", "Verified; ready to cut over")
    await db.commit()


def _missing_at_target(
    src: StorageContext, dst: StorageContext, src_prefix: str, dst_prefix: str
) -> list[str]:
    at_target = {
        uri[len(dst_prefix) :]: size
        for uri, size in list_objects(dst, dst_prefix)
        if uri.startswith(dst_prefix)
    }
    return [
        uri
        for uri, size in list_objects(src, src_prefix)
        if uri.startswith(src_prefix) and at_target.get(uri[len(src_prefix) :]) != size
    ]


async def cutover(db: AsyncSession, migration: CatalogMigration, log) -> None:  # noqa: ANN001
    """Point the catalog at the copied data.

    ``data_path`` and ``storage_backend_id`` live in different databases. The
    stored path goes first and is authoritative, so a crash between the two
    fails loudly against the target and re-entry is idempotent.
    """
    from api.services.catalog_backends.ducklake import get_engine

    catalog = await db.get(Catalog, migration.catalog_id)
    assert catalog
    target_path = migration.target_data_path
    assert target_path

    async with get_engine().begin() as conn:
        stored = (
            await conn.execute(
                text(
                    f'SELECT value FROM "{catalog.metadata_schema}".ducklake_metadata '
                    "WHERE key = 'data_path'"
                )
            )
        ).scalar_one_or_none()
        if stored != target_path:
            result = await conn.execute(
                text(
                    f'UPDATE "{catalog.metadata_schema}".ducklake_metadata '
                    "SET value = :path WHERE key = 'data_path'"
                ),
                {"path": target_path},
            )
            if result.rowcount != 1:
                raise RuntimeError(
                    "Expected exactly one data_path row in the catalog's metadata, "
                    f"updated {result.rowcount}."
                )

    now = datetime.now(tz=UTC)
    catalog.storage_backend_id = migration.target_storage_backend_id
    migration.status = "completed"
    migration.cutover_at = now
    migration.finished_at = now
    await log(db, migration.id, "info", "Cutover complete; catalog now served from the new backend")
    await db.commit()

    await set_agent_dml(catalog, allowed=True)


async def teardown(db: AsyncSession, migration: CatalogMigration, *, which: str) -> None:
    """Delete one side's prefix. ``which`` is "target" (abandon) or "source" (retention)."""
    from api.services.migration.storage_io import delete_prefix

    catalog = await db.get(Catalog, migration.catalog_id)
    if catalog is None:
        return
    if which == "target":
        backend = await db.get(StorageBackend, migration.target_storage_backend_id)
        path = migration.target_data_path
    else:
        backend = await db.get(StorageBackend, migration.source_storage_backend_id)
        path = migration.source_data_path
    if backend is None or not path:
        return
    try:
        ctx = _context(backend, path)
        await asyncio.to_thread(delete_prefix, ctx, path)
    except Exception:  # noqa: BLE001 - best-effort, like the Iceberg teardown
        logger.warning("Best-effort %s teardown failed for %s", which, path, exc_info=True)


async def restore_writes(db: AsyncSession, migration: CatalogMigration) -> None:
    """Re-grant DML after a failed or cancelled migration."""
    catalog = await db.get(Catalog, migration.catalog_id)
    if catalog is None:
        return
    try:
        await set_agent_dml(catalog, allowed=True)
    except Exception:  # noqa: BLE001 - never block the failure path
        logger.warning("Could not restore writes on %s", catalog.slug, exc_info=True)


def parsed_prefix(path: str) -> str:
    """The key portion of a data path, for comparing against storage listings."""
    return urlparse(path).path.lstrip("/")
