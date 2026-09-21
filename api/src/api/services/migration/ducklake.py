"""Migrating a DuckLake catalog between storage backends.

Structurally simpler than the Iceberg path, and the reason is worth stating: a
DuckLake data file's location is relative to its table's path, which is relative
to its schema's, which is relative to a ``data_path`` recorded once in
``ducklake_metadata``. Moving the catalog is therefore a prefix copy plus one row
update — no shadow catalog, no metadata tree to rewrite, no re-registration.

The phases, statuses, per-table checkpoints, event log, cancel and runner loop
are the Iceberg path's, unchanged. Only the bodies differ, so the admin UI and
everything else keeps working without knowing a second kind exists.

Per DuckLake **table**, not per object: table paths nest under the data path, so
each table is a prefix, ``tables_done``/``tables_total`` keep their literal
meaning in the existing progress bar, and a mid-table crash resumes cheaply
because the copy skips objects already present at the target.
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

# How long to wait for in-flight statements to drain before copying. The
# submit-time freeze cannot catch a statement that was already running when the
# migration row committed, and on DuckLake that write lands under the source
# prefix *after* the copy started.
QUIESCE_TIMEOUT_S = 300.0


async def absolute_path_count(catalog: Catalog) -> int:
    """Live data and delete files recorded with an absolute path.

    These do not move when ``data_path`` changes — that is what absolute means —
    so they would be silently left behind pointing at the old location. They
    arrive via `ducklake_add_data_files`, which registers existing Parquet
    without copying it, and can name a bucket the target credentials cannot even
    reach. Refused up front rather than rewritten on a guess.
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

    The structural half of the freeze. The submit-time gate is a check the
    control plane makes; this is one PostgreSQL makes, so an agent that somehow
    dispatches during the copy gets a permission error rather than committing a
    write the copy has already passed.
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
    # Fetched by id rather than through `catalog.storage_backend`: that
    # relationship is lazy, and the runner loads the catalog without it, so
    # touching it here raises MissingGreenlet in async context. The Iceberg
    # path takes the ids for the same reason.
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

    # Stay in `pending` while anything is still running: the runner retries in
    # 30s, so this costs a tick rather than a busy-wait.
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

    # Catalog-level files live directly under the data path and belong to no
    # table; without this sweep they would be left behind.
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
    """Copy every object under a prefix, skipping ones already the right size.

    Copy-if-absent, as the Iceberg path does, so a crashed migration resumes
    without re-moving what it already moved.
    """
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

    # Re-checked here as well as up front: a write that slipped the freeze could
    # have registered an absolute path while the copy was running.
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

    Two databases are involved and cannot share a transaction: ``data_path``
    lives in the catalog database, ``storage_backend_id`` in DuckHaven's. The
    stored path is updated first and is authoritative — the agent no longer
    passes DATA_PATH for an initialised catalog — so a crash between the two
    leaves reads failing loudly against the target rather than silently reading
    the wrong place, and re-entry is idempotent.
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

    # Writes were revoked for the copy; the catalog is live again.
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
    """Re-grant DML after a failed or cancelled migration.

    Without this the catalog stays read-only at the PostgreSQL level after a
    failure, which looks exactly like corruption to whoever tries to write next.
    """
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
