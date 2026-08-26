"""API-facing helpers for catalog migration: start, query, cancel, freeze check.

The runner/engine own execution; this module owns the request-time concerns the
routers call into — validating a start request, listing/fetching migrations and
their logs, requesting cancellation, and the freeze-gate lookup that the query
path uses to reject writes against a catalog with an in-flight migration.
"""

from __future__ import annotations

import uuid

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.catalog import Catalog, WorkspaceCatalog
from api.models.catalog_migration import CatalogMigration, CatalogMigrationEvent
from api.models.storage_backend import StorageBackend
from api.services.migration import ACTIVE_STATUSES, STATUS_CUTOVER, TERMINAL_STATUSES
from api.services.polaris import PolarisClient
from api.services.storage_health import validate_backend


async def active_migration(db: AsyncSession, catalog_id: uuid.UUID) -> CatalogMigration | None:
    return (
        await db.execute(
            select(CatalogMigration).where(
                CatalogMigration.catalog_id == catalog_id,
                CatalogMigration.status.in_(ACTIVE_STATUSES),
            )
        )
    ).scalar_one_or_none()


async def workspace_has_active_migration(db: AsyncSession, workspace_id: uuid.UUID) -> bool:
    """Whether any catalog attached to the workspace is mid-migration — the
    read-only freeze gate consults this to reject writes."""
    row = await db.execute(
        select(CatalogMigration.id)
        .join(WorkspaceCatalog, WorkspaceCatalog.catalog_id == CatalogMigration.catalog_id)
        .where(
            WorkspaceCatalog.workspace_id == workspace_id,
            CatalogMigration.status.in_(ACTIVE_STATUSES),
        )
        .limit(1)
    )
    return row.first() is not None


async def start_migration(
    db: AsyncSession,
    polaris: PolarisClient,
    *,
    catalog: Catalog,
    target_backend: StorageBackend,
    created_by: uuid.UUID,
) -> CatalogMigration:
    """Validate and create a migration record (the runner picks it up). Refuses a
    no-op target, a second concurrent migration, or an unreachable target."""
    if target_backend.id == catalog.storage_backend_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Catalog is already on this storage backend.",
        )
    if await active_migration(db, catalog.id) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A migration is already in progress for this catalog.",
        )
    health = await validate_backend(polaris, target_backend)
    if not health.valid:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Target storage backend is not usable: {health.detail}",
        )

    migration = CatalogMigration(
        catalog_id=catalog.id,
        source_storage_backend_id=catalog.storage_backend_id,
        target_storage_backend_id=target_backend.id,
        created_by=created_by,
    )
    db.add(migration)
    await db.flush()
    return migration


async def get_migration(
    db: AsyncSession, catalog_id: uuid.UUID, migration_id: uuid.UUID
) -> CatalogMigration:
    migration = await db.get(CatalogMigration, migration_id)
    if migration is None or migration.catalog_id != catalog_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Migration not found")
    return migration


async def request_cancel(db: AsyncSession, migration: CatalogMigration) -> None:
    """Flag a migration for cancellation. Allowed only before cutover begins; the
    runner stops between tables and tears the shadow catalog down."""
    if migration.status in TERMINAL_STATUSES or migration.status == STATUS_CUTOVER:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Migration can no longer be cancelled.",
        )
    migration.cancel_requested = True
    await db.flush()


async def list_events(
    db: AsyncSession, migration_id: uuid.UUID, after: int = 0
) -> list[CatalogMigrationEvent]:
    """Log events with ``seq > after``, oldest first — the incremental cursor the
    UI log viewer polls with."""
    return list(
        (
            await db.execute(
                select(CatalogMigrationEvent)
                .where(
                    CatalogMigrationEvent.migration_id == migration_id,
                    CatalogMigrationEvent.seq > after,
                )
                .order_by(CatalogMigrationEvent.seq)
            )
        )
        .scalars()
        .all()
    )
