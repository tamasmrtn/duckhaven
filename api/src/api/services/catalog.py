"""Catalog lifecycle: create, attach/detach, drop.

A catalog is a decoupled, first-class entity (its own Polaris catalog + storage
backend) bound to workspaces M:N via ``WorkspaceCatalog``. Authorization is the
caller's responsibility (routers gate on workspace role); this layer owns the
Polaris provisioning + the single-default-per-workspace invariant.
"""

from __future__ import annotations

import logging
import uuid

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from api.models.catalog import Catalog, WorkspaceCatalog
from api.models.storage_backend import StorageBackend
from api.models.workspace import Workspace
from api.services.polaris import PolarisClient, PolarisError, PolarisNotFoundError
from api.services.workspace import (
    ensure_polaris_catalog,
    polaris_storage,
    validate_catalog_slug,
)

logger = logging.getLogger(__name__)


async def create_catalog(
    db: AsyncSession,
    polaris: PolarisClient,
    *,
    slug: str,
    name: str,
    backend: StorageBackend,
    created_by: uuid.UUID,
    polaris_name: str | None = None,
) -> Catalog:
    """Provision a new catalog's Polaris catalog + default namespace and persist
    its record. ``polaris_name`` defaults to the (globally-unique, identifier-safe)
    slug; a workspace's default catalog overrides it to the workspace slug so the
    Polaris catalog is named after the workspace (legacy parity). Rolls back the
    pg row if Polaris provisioning fails (D7)."""
    validate_catalog_slug(slug)
    existing = await db.execute(select(Catalog).where(Catalog.slug == slug))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=f"Catalog slug '{slug}' already taken"
        )

    polaris_name = polaris_name or slug
    dupe_name = await db.execute(select(Catalog).where(Catalog.polaris_name == polaris_name))
    if dupe_name.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Catalog name '{polaris_name}' already taken",
        )
    storage_type, base_location, extra_storage = polaris_storage(backend.kind, backend.root_uri)
    try:
        await ensure_polaris_catalog(
            polaris,
            polaris_name,
            storage_type=storage_type,
            base_location=base_location,
            extra_storage=extra_storage,
        )
    except PolarisError as exc:
        logger.warning("Polaris provisioning failed for catalog=%s: %s", slug, exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Polaris provisioning failed: {exc}",
        ) from exc

    catalog = Catalog(
        slug=slug,
        name=name,
        polaris_name=polaris_name,
        storage_backend_id=backend.id,
        created_by=created_by,
    )
    db.add(catalog)
    await db.flush()
    return catalog


async def attach_catalog(
    db: AsyncSession,
    *,
    workspace: Workspace,
    catalog: Catalog,
    attached_by: uuid.UUID,
    make_default: bool = False,
) -> WorkspaceCatalog:
    """Bind ``catalog`` to ``workspace``. The first catalog attached to a
    workspace becomes its default; ``make_default`` re-points the default."""
    dupe = await db.execute(
        select(WorkspaceCatalog).where(
            WorkspaceCatalog.workspace_id == workspace.id,
            WorkspaceCatalog.catalog_id == catalog.id,
        )
    )
    if dupe.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Catalog '{catalog.slug}' is already attached to this workspace.",
        )

    count = await db.scalar(
        select(func.count())
        .select_from(WorkspaceCatalog)
        .where(WorkspaceCatalog.workspace_id == workspace.id)
    )
    is_default = make_default or count == 0
    if is_default:
        await _clear_default(db, workspace.id)

    link = WorkspaceCatalog(
        workspace_id=workspace.id,
        catalog_id=catalog.id,
        is_default=is_default,
        attached_by=attached_by,
    )
    db.add(link)
    await db.flush()
    return link


async def detach_catalog(db: AsyncSession, *, workspace: Workspace, catalog: Catalog) -> None:
    """Unbind ``catalog`` from ``workspace``. If it was the default, promote the
    next remaining catalog (by slug) so the workspace keeps a default."""
    link = (
        await db.execute(
            select(WorkspaceCatalog).where(
                WorkspaceCatalog.workspace_id == workspace.id,
                WorkspaceCatalog.catalog_id == catalog.id,
            )
        )
    ).scalar_one_or_none()
    if link is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    was_default = link.is_default
    await db.delete(link)
    await db.flush()
    if was_default:
        nxt = (
            await db.execute(
                select(WorkspaceCatalog)
                .join(Catalog, Catalog.id == WorkspaceCatalog.catalog_id)
                .where(WorkspaceCatalog.workspace_id == workspace.id)
                .order_by(Catalog.slug)
                .limit(1)
            )
        ).scalar_one_or_none()
        if nxt is not None:
            nxt.is_default = True
    await db.flush()


async def drop_catalog(db: AsyncSession, polaris: PolarisClient, *, catalog: Catalog) -> None:
    """Permanently delete a catalog. Refused while it is attached to any
    workspace, so a shared catalog is never dropped out from under a peer."""
    bindings = await db.scalar(
        select(func.count())
        .select_from(WorkspaceCatalog)
        .where(WorkspaceCatalog.catalog_id == catalog.id)
    )
    if bindings:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Catalog '{catalog.slug}' is still attached to {bindings} workspace(s). "
                "Detach it everywhere before dropping."
            ),
        )
    try:
        await polaris.delete_catalog(catalog.polaris_name)
    except PolarisNotFoundError:
        pass
    except PolarisError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Polaris catalog deletion failed: {exc}",
        ) from exc
    await db.delete(catalog)
    await db.flush()


async def list_attachable(db: AsyncSession) -> list[Catalog]:
    """Every catalog in the deployment (the attach picker's source)."""
    rows = await db.execute(
        select(Catalog).options(selectinload(Catalog.storage_backend)).order_by(Catalog.slug)
    )
    return list(rows.scalars().all())


async def _clear_default(db: AsyncSession, workspace_id: uuid.UUID) -> None:
    rows = await db.execute(
        select(WorkspaceCatalog).where(
            WorkspaceCatalog.workspace_id == workspace_id,
            WorkspaceCatalog.is_default.is_(True),
        )
    )
    for link in rows.scalars().all():
        link.is_default = False
    await db.flush()
