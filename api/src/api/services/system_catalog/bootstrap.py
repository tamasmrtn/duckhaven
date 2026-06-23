"""Provisioning + lifecycle for the system catalog (``duckhaven``).

Two entry points:

- :func:`provision_system_catalog` runs once, during first-admin setup, with the
  storage backend the admin chose for the catalog. It creates the catalog's
  Postgres rows, provisions Polaris, and links every workspace.
- :func:`ensure_system_catalog` runs on every API startup. It only *self-heals*
  an already-provisioned catalog (re-asserts Polaris namespaces + workspace
  links); on a fresh deployment, where the admin has not set up storage yet, it
  is a no-op. This is why the migration only adds columns — it never has to know
  about Polaris or pick a storage backend.
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from api.models.catalog import Catalog, WorkspaceCatalog
from api.models.storage_backend import StorageBackend
from api.models.workspace import Workspace
from api.services.polaris import PolarisClient, PolarisConflictError, PolarisError
from api.services.system_catalog.constants import (
    SYSTEM_CATALOG_NAME,
    SYSTEM_CATALOG_SLUG,
    SYSTEM_NAMESPACES,
)
from api.services.workspace import ensure_polaris_catalog, polaris_storage

logger = logging.getLogger(__name__)


async def get_system_catalog(db: AsyncSession) -> Catalog | None:
    """The system catalog row (with its storage backend loaded), or None when it
    has not been provisioned yet (no admin setup has run)."""
    row = await db.execute(
        select(Catalog)
        .where(Catalog.is_system.is_(True))
        .options(selectinload(Catalog.storage_backend))
    )
    return row.scalar_one_or_none()


async def link_system_catalog(db: AsyncSession, workspace_id: uuid.UUID) -> None:
    """Bind the system catalog to ``workspace_id`` (never the default). No-op when
    the system catalog does not exist yet or the link already exists. The caller
    commits.
    """
    catalog = await get_system_catalog(db)
    if catalog is None:
        return
    existing = await db.execute(
        select(WorkspaceCatalog).where(
            WorkspaceCatalog.workspace_id == workspace_id,
            WorkspaceCatalog.catalog_id == catalog.id,
        )
    )
    if existing.scalar_one_or_none() is not None:
        return
    db.add(WorkspaceCatalog(workspace_id=workspace_id, catalog_id=catalog.id, is_default=False))


async def provision_system_catalog(
    db: AsyncSession,
    polaris: PolarisClient,
    *,
    backend: StorageBackend,
    created_by: uuid.UUID | None,
) -> Catalog:
    """Create the system catalog on the admin-chosen ``backend`` and attach it to
    every workspace. Called once during first-admin setup.

    The Postgres rows are persisted even if Polaris provisioning fails, so the
    admin's storage choice is not lost — the startup self-heal completes Polaris
    on the next boot. Idempotent: a second call returns the existing catalog.
    """
    catalog = await get_system_catalog(db)
    if catalog is None:
        db.add(backend)
        await db.flush()
        catalog = Catalog(
            slug=SYSTEM_CATALOG_SLUG,
            name=SYSTEM_CATALOG_NAME,
            polaris_name=SYSTEM_CATALOG_SLUG,
            storage_backend_id=backend.id,
            is_system=True,
            created_by=created_by,
        )
        db.add(catalog)
        await db.flush()
    await _provision_polaris(polaris, catalog, best_effort=True)
    await _backfill_workspace_links(db, catalog)
    await db.commit()
    await db.refresh(catalog, attribute_names=["storage_backend"])
    return catalog


async def ensure_system_catalog(db: AsyncSession, polaris: PolarisClient) -> Catalog | None:
    """Startup self-heal: re-assert Polaris namespaces + workspace links for an
    already-provisioned system catalog. No-op (returns None) on a deployment
    where the admin has not chosen storage yet.
    """
    catalog = await get_system_catalog(db)
    if catalog is None:
        logger.info("System catalog not provisioned yet; awaiting first-admin setup")
        return None
    await _provision_polaris(polaris, catalog, best_effort=True)
    await _backfill_workspace_links(db, catalog)
    await db.commit()
    return catalog


async def _provision_polaris(
    polaris: PolarisClient, catalog: Catalog, *, best_effort: bool
) -> None:
    """Idempotently create the Polaris catalog + its namespaces. ``best_effort``
    swallows Polaris errors (the Postgres rows already persist the intent; the
    next startup retries)."""
    backend = catalog.storage_backend
    storage_type, base_location, extra = polaris_storage(backend.kind, backend.root_uri)
    try:
        await ensure_polaris_catalog(
            polaris,
            catalog.polaris_name,
            storage_type=storage_type,
            base_location=base_location,
            extra_storage=extra,
            default_schema=SYSTEM_NAMESPACES[0],
        )
        for namespace in SYSTEM_NAMESPACES[1:]:
            try:
                await polaris.create_schema(catalog.polaris_name, namespace)
            except PolarisConflictError:
                pass
    except PolarisError as exc:
        if not best_effort:
            raise
        logger.warning("System catalog Polaris provisioning deferred: %s", exc)


async def _backfill_workspace_links(db: AsyncSession, catalog: Catalog) -> None:
    """Insert a (non-default) link for every workspace not already bound."""
    linked = set(
        (
            await db.execute(
                select(WorkspaceCatalog.workspace_id).where(
                    WorkspaceCatalog.catalog_id == catalog.id
                )
            )
        )
        .scalars()
        .all()
    )
    workspace_ids = (await db.execute(select(Workspace.id))).scalars().all()
    for ws_id in workspace_ids:
        if ws_id not in linked:
            db.add(WorkspaceCatalog(workspace_id=ws_id, catalog_id=catalog.id, is_default=False))
