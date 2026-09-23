"""Catalog lifecycle: create, attach/detach, drop.

A catalog is a decoupled, first-class entity (one catalog kind + one storage
backend) bound to workspaces M:N via ``WorkspaceCatalog``. Authorization is the
caller's responsibility (routers gate on workspace role); this layer owns the
single-default-per-workspace invariant and defers provisioning to the catalog's
own backend (``services/catalog_backends``).
"""

from __future__ import annotations

import logging
import uuid

from fastapi import HTTPException, status
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from api.config import settings
from api.models.catalog import (
    KIND_DUCKLAKE,
    KIND_ICEBERG_POLARIS,
    Catalog,
    WorkspaceCatalog,
)
from api.models.maintenance import MaintenanceRecommendation, TableHealthSample
from api.models.storage_backend import StorageBackend
from api.models.table_metadata import TableMetadata
from api.models.user import Credential
from api.models.workspace import Workspace
from api.services.catalog_backends import (
    CatalogBackendError,
    backend_for,
    capabilities_for,
)
from api.services.catalog_backends.ducklake import metadata_schema_for, new_role_password
from api.services.polaris import PolarisClient
from api.services.workspace import validate_catalog_slug

logger = logging.getLogger(__name__)


async def create_catalog(
    db: AsyncSession,
    polaris: PolarisClient,
    *,
    name: str,
    backend: StorageBackend,
    created_by: uuid.UUID,
    kind: str = KIND_ICEBERG_POLARIS,
) -> Catalog:
    """Provision a new catalog in its metastore and persist its record.

    ``name`` doubles as the slug and, per kind, the Polaris name or metadata
    schema. No row is written if provisioning fails (D7)."""
    validate_catalog_slug(name)
    if kind == KIND_DUCKLAKE and not settings.ducklake_enabled:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "error": "ducklake_disabled",
                "detail": (
                    "DuckLake catalogs are not enabled on this deployment. "
                    "Set DUCKLAKE_ENABLED=true (see docs/deployment/ducklake.md)."
                ),
            },
        )
    supported = capabilities_for(kind).supported_storage_kinds
    if backend.kind not in supported:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                f"A {kind} catalog cannot use a {backend.kind} storage backend. "
                f"Supported: {', '.join(supported)}."
            ),
        )
    existing = await db.execute(select(Catalog).where(Catalog.slug == name))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=f"Catalog '{name}' already taken"
        )

    try:
        metadata_schema = metadata_schema_for(name) if kind == KIND_DUCKLAKE else None
    except CatalogBackendError as exc:
        # A name too long for a Postgres identifier is a 422, not a 502.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc

    catalog = Catalog(
        slug=name,
        name=name,
        kind=kind,
        # Exactly one identity per kind, per ck_catalogs_kind_identity.
        polaris_name=name if kind == KIND_ICEBERG_POLARIS else None,
        metadata_schema=metadata_schema,
        storage_backend_id=backend.id,
        created_by=created_by,
    )
    # Provision before flush so a failure leaves no row behind (D7).
    catalog.storage_backend = backend
    if kind == KIND_DUCKLAKE:
        catalog.pending_ducklake_password = new_role_password()
    try:
        await backend_for(catalog, polaris=polaris).provision(catalog)
    except CatalogBackendError as exc:
        logger.warning("Catalog provisioning failed for catalog=%s: %s", name, exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Catalog provisioning failed: {exc}",
        ) from exc

    db.add(catalog)
    await db.flush()
    if kind == KIND_DUCKLAKE:
        # Explicit: the relationship is view-only.
        db.add(
            Credential(
                kind="ducklake_role",
                token=catalog.pending_ducklake_password,
                catalog_id=catalog.id,
            )
        )
        await db.flush()
    return catalog


async def attach_catalog(
    db: AsyncSession,
    *,
    workspace: Workspace,
    catalog: Catalog,
    attached_by: uuid.UUID,
    make_default: bool = False,
    access_mode: str = "open",
) -> WorkspaceCatalog:
    """Bind ``catalog`` to ``workspace``. The first catalog attached to a
    workspace becomes its default; ``make_default`` re-points the default.

    ``access_mode`` defaults to ``open``, so attaching an existing catalog is
    unchanged; only catalog creation passes anything else.
    """
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
        access_mode=access_mode,
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
    # Load eagerly: a lazy relationship in async code raises MissingGreenlet.
    await db.refresh(catalog, attribute_names=["storage_backend"])
    try:
        await backend_for(catalog, polaris=polaris).deprovision(catalog)
    except CatalogBackendError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Catalog deletion failed: {exc}",
        ) from exc

    # Drop the catalog's control-plane sidecars (intrinsic per-catalog rows).
    for model in (TableMetadata, TableHealthSample, MaintenanceRecommendation):
        await db.execute(delete(model).where(model.catalog_id == catalog.id))
    await db.delete(catalog)
    await db.flush()


async def list_attachable(db: AsyncSession) -> list[Catalog]:
    """Every catalog in the deployment (the attach picker's source)."""
    rows = await db.execute(
        select(Catalog).options(selectinload(Catalog.storage_backend)).order_by(Catalog.slug)
    )
    return list(rows.scalars().all())


async def set_default_catalog(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    link: WorkspaceCatalog,
    is_default: bool,
) -> None:
    """Set or clear ``link`` as the workspace's default.

    Split out of :func:`attach_catalog` so re-attaching an already-attached
    catalog can move the default without the duplicate check refusing it: the
    attach route is a PUT, and a PUT has to be idempotent.

    Clearing promotes another attachment rather than leaving the workspace
    without one -- the same invariant :func:`detach_catalog` maintains, and the
    reason the schema resolution in ``target_catalog`` can assume a default
    exists wherever any catalog is attached.
    """
    if is_default:
        await _clear_default(db, workspace_id)
        link.is_default = True
        await db.flush()
        return

    link.is_default = False
    await db.flush()
    successor = (
        await db.execute(
            select(WorkspaceCatalog)
            .join(Catalog, Catalog.id == WorkspaceCatalog.catalog_id)
            .where(
                WorkspaceCatalog.workspace_id == workspace_id,
                WorkspaceCatalog.catalog_id != link.catalog_id,
            )
            .order_by(Catalog.slug)
            .limit(1)
        )
    ).scalar_one_or_none()
    if successor is not None:
        successor.is_default = True
    await db.flush()


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
