"""Drive one catalog migration through its phases.

``process_migration`` runs a single ``CatalogMigration`` from its current status
to a terminal state, committing a checkpoint after every meaningful step so a
crash resumes from the last completed table rather than restarting. It emits a
``CatalogMigrationEvent`` log line at each step (the operator-facing narrative).

Phases: ``pending`` (provision the shadow Polaris catalog at the target backend +
enumerate tables) → ``copying`` (copy+rewrite+register each table) → ``verifying``
(load each shadow table, snapshot counts match) → ``cutover`` (atomic re-point of
the catalog row) → ``completed``. The catalog's ``storage_backend_id`` /
``polaris_name`` change only in the final cutover transaction, so any earlier
failure leaves the catalog on the old backend untouched.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.catalog import Catalog
from api.models.catalog_migration import (
    CatalogMigration,
    CatalogMigrationEvent,
    CatalogMigrationTable,
)
from api.models.storage_backend import StorageBackend
from api.services.migration import (
    STATUS_CANCELLED,
    STATUS_COMPLETED,
    STATUS_COPYING,
    STATUS_CUTOVER,
    STATUS_FAILED,
    STATUS_PENDING,
    STATUS_VERIFYING,
    TABLE_PENDING,
    TABLE_REGISTERED,
    TABLE_VERIFIED,
    relocate,
)
from api.services.migration.storage_io import StorageContext
from api.services.polaris import (
    PolarisClient,
    PolarisConflictError,
    PolarisError,
    PolarisNotFoundError,
)
from api.services.workspace import ensure_polaris_catalog, polaris_storage

logger = logging.getLogger(__name__)

# Throwaway namespace/table on the shadow catalog whose sole purpose is to make
# Polaris vend catalog-scoped write credentials for the new location (mirrors the
# storage-health probe). Dropped before cutover so the migrated catalog is clean.
_PROBE_SCHEMA = "dh_migration"
_PROBE_TABLE = "probe"
_PROBE_COLUMNS = [{"id": 1, "name": "x", "required": False, "type": "int"}]


async def log_event(db: AsyncSession, migration_id: uuid.UUID, level: str, message: str) -> None:
    """Append a user-facing log line with the next monotonic ``seq``."""
    seq = (
        await db.scalar(
            sa.select(sa.func.coalesce(sa.func.max(CatalogMigrationEvent.seq), 0)).where(
                CatalogMigrationEvent.migration_id == migration_id
            )
        )
    ) or 0
    db.add(
        CatalogMigrationEvent(migration_id=migration_id, seq=seq + 1, level=level, message=message)
    )
    await db.flush()


def _cat_base(backend: StorageBackend, polaris_name: str) -> str:
    """The catalog's scoped storage root — matches ``ensure_polaris_catalog``."""
    _, base, _ = polaris_storage(backend.kind, backend.root_uri, backend.config)
    return f"{base.rstrip('/')}/{polaris_name}"


async def process_migration(
    db: AsyncSession, polaris: PolarisClient, migration: CatalogMigration
) -> None:
    """Advance one migration to a terminal state, or fail it cleanly."""
    try:
        if migration.cancel_requested and migration.status != STATUS_CUTOVER:
            await _cancel(db, polaris, migration)
            return
        if migration.status == STATUS_PENDING:
            await _provision(db, polaris, migration)
        if migration.status == STATUS_COPYING:
            if migration.cancel_requested:
                await _cancel(db, polaris, migration)
                return
            await _copy(db, polaris, migration)
        if migration.status == STATUS_VERIFYING:
            await _verify(db, polaris, migration)
        if migration.status == STATUS_CUTOVER:
            await _cutover(db, polaris, migration)
    except Exception as exc:  # noqa: BLE001 - any failure must leave a recoverable state
        await _fail(db, polaris, migration, exc)


async def _provision(db: AsyncSession, polaris: PolarisClient, migration: CatalogMigration) -> None:
    catalog = await db.get(Catalog, migration.catalog_id)
    source = await db.get(StorageBackend, migration.source_storage_backend_id)
    target = await db.get(StorageBackend, migration.target_storage_backend_id)
    if catalog is None or source is None or target is None:
        raise RuntimeError("Catalog or storage backend missing for migration")

    migration.started_at = datetime.now(tz=UTC)
    migration.source_polaris_name = catalog.polaris_name
    if migration.shadow_polaris_name is None:
        migration.shadow_polaris_name = f"{catalog.polaris_name}__m{uuid.uuid4().hex[:8]}"[:255]
    shadow = migration.shadow_polaris_name
    await log_event(
        db,
        migration.id,
        "info",
        f"Provisioning shadow catalog '{shadow}' on backend '{target.name}'",
    )
    # Persist the shadow name *before* creating it in Polaris, so a failure here
    # still leaves the name on the record for the cleanup teardown to remove.
    await db.commit()

    storage_type, base, extra = polaris_storage(target.kind, target.root_uri, target.config)
    await ensure_polaris_catalog(
        polaris, shadow, storage_type=storage_type, base_location=base, extra_storage=extra
    )

    # Mirror every source namespace into the shadow (registerTable needs the
    # namespace to exist) and enumerate the tables to copy.
    schemas = await polaris.list_schemas(catalog.polaris_name)
    total = 0
    for schema in schemas:
        try:
            await polaris.create_schema(shadow, schema.name)
        except PolarisConflictError:
            pass
        for table in await polaris.list_tables(catalog.polaris_name, schema.name):
            db.add(
                CatalogMigrationTable(
                    migration_id=migration.id,
                    schema_name=schema.name,
                    table_name=table.name,
                    status=TABLE_PENDING,
                )
            )
            total += 1

    # Probe table → catalog-scoped vended write credentials for the new location.
    try:
        await polaris.create_schema(shadow, _PROBE_SCHEMA)
    except PolarisConflictError:
        pass
    try:
        await polaris.create_table(
            catalog=shadow, schema=_PROBE_SCHEMA, name=_PROBE_TABLE, columns=_PROBE_COLUMNS
        )
    except PolarisConflictError:
        pass

    migration.tables_total = total
    migration.status = STATUS_COPYING
    await log_event(db, migration.id, "info", f"Copying {total} table(s) to the new backend")
    await db.commit()


async def _copy(db: AsyncSession, polaris: PolarisClient, migration: CatalogMigration) -> None:
    catalog = await db.get(Catalog, migration.catalog_id)
    source = await db.get(StorageBackend, migration.source_storage_backend_id)
    target = await db.get(StorageBackend, migration.target_storage_backend_id)
    assert catalog and source and target
    shadow = migration.shadow_polaris_name
    assert shadow

    old_prefix = _cat_base(source, catalog.polaris_name)
    new_prefix = _cat_base(target, shadow)

    pending = (
        (
            await db.execute(
                sa.select(CatalogMigrationTable)
                .where(
                    CatalogMigrationTable.migration_id == migration.id,
                    CatalogMigrationTable.status.notin_([TABLE_REGISTERED, TABLE_VERIFIED]),
                )
                .order_by(CatalogMigrationTable.schema_name, CatalogMigrationTable.table_name)
            )
        )
        .scalars()
        .all()
    )

    for mt in pending:
        if migration.cancel_requested:
            await _cancel(db, polaris, migration)
            return

        # Source read creds (table-scoped) + destination write creds (the shadow
        # probe yields catalog-scoped creds for the whole new base).
        src_body = await polaris.load_table_with_credentials(
            catalog.polaris_name, mt.schema_name, mt.table_name
        )
        dst_body = await polaris.load_table_with_credentials(shadow, _PROBE_SCHEMA, _PROBE_TABLE)
        src_meta = src_body.get("metadata") or {}
        src_location = src_meta.get("location")
        src_metadata_location = src_body.get("metadata-location")
        if not src_location or not src_metadata_location:
            raise RuntimeError(f"Polaris returned no location for {mt.schema_name}.{mt.table_name}")

        src_ctx = StorageContext(source.kind, src_body.get("config") or {}, source.config or {})
        dst_ctx = StorageContext(target.kind, dst_body.get("config") or {}, target.config or {})

        result = await asyncio.to_thread(
            relocate.relocate_table,
            source_location=src_location,
            source_metadata_location=src_metadata_location,
            old_prefix=old_prefix,
            new_prefix=new_prefix,
            src_ctx=src_ctx,
            dst_ctx=dst_ctx,
        )
        mt.source_metadata_location = src_metadata_location
        mt.target_metadata_location = result.target_metadata_location
        mt.bytes_copied = result.bytes_copied
        migration.bytes_copied += result.bytes_copied

        # Adopt the rewritten metadata into the shadow catalog. A conflict means a
        # prior attempt already registered it (resume) — treat as done.
        try:
            await polaris.register_table(
                shadow, mt.schema_name, mt.table_name, result.target_metadata_location
            )
        except PolarisConflictError:
            pass
        mt.status = TABLE_REGISTERED
        migration.tables_done += 1
        await log_event(
            db,
            migration.id,
            "info",
            f"Copied and registered {mt.schema_name}.{mt.table_name} "
            f"({migration.tables_done}/{migration.tables_total})",
        )
        await db.commit()

    migration.status = STATUS_VERIFYING
    await log_event(db, migration.id, "info", "All tables copied; verifying")
    await db.commit()


async def _verify(db: AsyncSession, polaris: PolarisClient, migration: CatalogMigration) -> None:
    catalog = await db.get(Catalog, migration.catalog_id)
    assert catalog
    shadow = migration.shadow_polaris_name
    assert shadow

    unverified = (
        (
            await db.execute(
                sa.select(CatalogMigrationTable).where(
                    CatalogMigrationTable.migration_id == migration.id,
                    CatalogMigrationTable.status != TABLE_VERIFIED,
                )
            )
        )
        .scalars()
        .all()
    )

    for mt in unverified:
        # Loading the shadow table proves the rewritten metadata resolves; the
        # snapshot counts matching proves history was preserved, not rebuilt.
        await polaris.get_table(shadow, mt.schema_name, mt.table_name)
        src_snaps = await polaris.list_snapshots(
            catalog.polaris_name, mt.schema_name, mt.table_name
        )
        dst_snaps = await polaris.list_snapshots(shadow, mt.schema_name, mt.table_name)
        if len(src_snaps) != len(dst_snaps):
            raise RuntimeError(
                f"Snapshot count mismatch for {mt.schema_name}.{mt.table_name}: "
                f"source={len(src_snaps)} target={len(dst_snaps)}"
            )
        mt.status = TABLE_VERIFIED
        await db.commit()

    migration.status = STATUS_CUTOVER
    await log_event(db, migration.id, "info", "Verification passed; cutting over")
    await db.commit()


async def _cutover(db: AsyncSession, polaris: PolarisClient, migration: CatalogMigration) -> None:
    catalog = await db.get(Catalog, migration.catalog_id)
    assert catalog
    shadow = migration.shadow_polaris_name
    assert shadow

    # Drop the probe (best-effort) so the migrated catalog has no stray table.
    try:
        await polaris.delete_table(shadow, _PROBE_SCHEMA, _PROBE_TABLE, purge=True)
        await polaris.delete_schema(shadow, _PROBE_SCHEMA)
    except PolarisError:
        pass

    # The atomic flip: from here the catalog reads/writes the new backend. The old
    # Polaris catalog + data are left intact for the retention window (rollback).
    now = datetime.now(tz=UTC)
    catalog.polaris_name = shadow
    catalog.storage_backend_id = migration.target_storage_backend_id
    migration.status = STATUS_COMPLETED
    migration.cutover_at = now
    migration.finished_at = now
    await log_event(
        db, migration.id, "info", "Cutover complete; catalog now served from the new backend"
    )
    await db.commit()


async def _cancel(db: AsyncSession, polaris: PolarisClient, migration: CatalogMigration) -> None:
    await _teardown_shadow(polaris, migration.shadow_polaris_name)
    migration.status = STATUS_CANCELLED
    migration.finished_at = datetime.now(tz=UTC)
    await log_event(db, migration.id, "warning", "Migration cancelled; shadow catalog removed")
    await db.commit()


async def _fail(
    db: AsyncSession, polaris: PolarisClient, migration: CatalogMigration, exc: Exception
) -> None:
    logger.exception("Catalog migration %s failed", migration.id)
    # Roll back any half-applied in-flight changes before recording the failure.
    await db.rollback()
    migration = await db.get(CatalogMigration, migration.id)
    if migration is None or migration.status in (STATUS_COMPLETED, STATUS_CANCELLED):
        return
    await _teardown_shadow(polaris, migration.shadow_polaris_name)
    migration.status = STATUS_FAILED
    migration.error = " ".join(str(exc).split())[:1000]
    migration.finished_at = datetime.now(tz=UTC)
    await log_event(db, migration.id, "error", f"Migration failed: {migration.error}")
    await db.commit()


async def cleanup_retained(
    db: AsyncSession, polaris: PolarisClient, *, older_than: datetime
) -> int:
    """Drop the retained old Polaris catalog of completed migrations whose
    retention window has elapsed. Idempotent: ``source_polaris_name`` is cleared
    once dropped, so a catalog is never torn down twice. Returns the count swept."""
    rows = (
        (
            await db.execute(
                sa.select(CatalogMigration).where(
                    CatalogMigration.status == STATUS_COMPLETED,
                    CatalogMigration.source_polaris_name.isnot(None),
                    CatalogMigration.cutover_at.isnot(None),
                    CatalogMigration.cutover_at < older_than,
                )
            )
        )
        .scalars()
        .all()
    )
    for migration in rows:
        await _teardown_shadow(polaris, migration.source_polaris_name)
        await log_event(
            db,
            migration.id,
            "info",
            f"Retention elapsed; dropped old catalog '{migration.source_polaris_name}'",
        )
        migration.source_polaris_name = None
        await db.commit()
    return len(rows)


async def _teardown_shadow(polaris: PolarisClient, shadow: str | None) -> None:
    """Best-effort removal of a shadow catalog and its copied files (purge)."""
    if not shadow:
        return
    try:
        for schema in await polaris.list_schemas(shadow):
            for table in await polaris.list_tables(shadow, schema.name):
                await polaris.delete_table(shadow, schema.name, table.name, purge=True)
            await polaris.delete_schema(shadow, schema.name)
        await polaris.delete_catalog_access(shadow)
        await polaris.delete_catalog(shadow)
    except PolarisNotFoundError:
        pass
    except PolarisError:
        logger.warning("Best-effort shadow teardown failed for %s", shadow, exc_info=True)
