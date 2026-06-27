"""Catalog lifecycle endpoints: list / create / attach / detach / drop.

Catalogs are decoupled, first-class entities bound to workspaces M:N. All
mutations are gated by the *existing* workspace-role check (no new RBAC):
create/attach/detach require workspace `owner`; a global drop requires the
catalog's creator or an admin and is refused while any binding remains.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from api.deps import get_current_user, get_db, get_polaris_client
from api.models.catalog import Catalog, WorkspaceCatalog
from api.models.storage_backend import StorageBackend
from api.models.user import User
from api.schemas.catalog_mgmt import CatalogAttachRequest, CatalogCreate, CatalogOut
from api.services import catalog as catalog_service
from api.services.permissions import Permission
from api.services.polaris import PolarisClient
from api.services.rbac import has_permission
from api.services.workspace import (
    assert_workspace_member,
    default_object_store_backend,
    get_workspace,
    resolve_catalog,
    resolve_workspace_catalogs,
)

router = APIRouter()


async def _binding_count(db: AsyncSession, catalog_id: uuid.UUID) -> int:
    return int(
        await db.scalar(
            select(func.count())
            .select_from(WorkspaceCatalog)
            .where(WorkspaceCatalog.catalog_id == catalog_id)
        )
        or 0
    )


def _catalog_out(
    catalog: Catalog, *, is_default: bool = False, attached_workspaces: int | None = None
) -> CatalogOut:
    return CatalogOut(
        id=catalog.id,
        slug=catalog.slug,
        name=catalog.name,
        polaris_name=catalog.polaris_name,
        storage_backend_id=catalog.storage_backend_id,
        storage_backend_kind=catalog.storage_backend.kind,
        storage_backend_name=catalog.storage_backend.name,
        storage_backend_root_uri=catalog.storage_backend.root_uri,
        created_at=catalog.created_at,
        is_default=is_default,
        attached_workspaces=attached_workspaces,
    )


@router.get("/catalogs", response_model=list[CatalogOut])
async def list_all_catalogs(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[CatalogOut]:
    """Every catalog in the deployment — the source for the attach picker."""
    catalogs = await catalog_service.list_attachable(db)
    return [_catalog_out(c, attached_workspaces=await _binding_count(db, c.id)) for c in catalogs]


@router.get("/workspaces/{ws}/catalogs", response_model=list[CatalogOut])
async def list_workspace_catalogs(
    ws: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[CatalogOut]:
    workspace = await get_workspace(db, ws)
    if workspace is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    await assert_workspace_member(db, workspace.id, user.id)
    catalogs = await resolve_workspace_catalogs(db, workspace.id)
    defaults = {
        link.catalog_id
        for link in (
            await db.execute(
                select(WorkspaceCatalog).where(
                    WorkspaceCatalog.workspace_id == workspace.id,
                    WorkspaceCatalog.is_default.is_(True),
                )
            )
        )
        .scalars()
        .all()
    }
    return [
        _catalog_out(
            c, is_default=c.id in defaults, attached_workspaces=await _binding_count(db, c.id)
        )
        for c in catalogs
    ]


@router.post(
    "/workspaces/{ws}/catalogs", response_model=CatalogOut, status_code=status.HTTP_201_CREATED
)
async def create_workspace_catalog(
    ws: str,
    body: CatalogCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    polaris: PolarisClient = Depends(get_polaris_client),
) -> CatalogOut:
    """Create a new catalog and attach it to the workspace."""
    workspace = await get_workspace(db, ws)
    if workspace is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    await assert_workspace_member(db, workspace.id, user.id, min_role="owner")

    if body.storage_backend_id is None:
        backend = default_object_store_backend(body.name, user.id)
        db.add(backend)
        await db.flush()
    else:
        backend = await db.get(StorageBackend, body.storage_backend_id)
        if backend is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Unknown storage backend"
            )

    catalog = await catalog_service.create_catalog(
        db, polaris, name=body.name, backend=backend, created_by=user.id
    )
    link = await catalog_service.attach_catalog(
        db, workspace=workspace, catalog=catalog, attached_by=user.id
    )
    await db.commit()
    await db.refresh(catalog, attribute_names=["storage_backend"])
    return _catalog_out(catalog, is_default=link.is_default, attached_workspaces=1)


@router.post("/workspaces/{ws}/catalogs/attach", response_model=CatalogOut)
async def attach_workspace_catalog(
    ws: str,
    body: CatalogAttachRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CatalogOut:
    """Attach an existing catalog to the workspace (M:N sharing)."""
    workspace = await get_workspace(db, ws)
    if workspace is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    await assert_workspace_member(db, workspace.id, user.id, min_role="owner")
    catalog = (
        await db.execute(
            select(Catalog)
            .where(Catalog.id == body.catalog_id)
            .options(selectinload(Catalog.storage_backend))
        )
    ).scalar_one_or_none()
    if catalog is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Catalog not found")
    link = await catalog_service.attach_catalog(
        db,
        workspace=workspace,
        catalog=catalog,
        attached_by=user.id,
        make_default=body.make_default,
    )
    await db.commit()
    return _catalog_out(
        catalog,
        is_default=link.is_default,
        attached_workspaces=await _binding_count(db, catalog.id),
    )


@router.delete("/workspaces/{ws}/catalogs/{catalog}", status_code=status.HTTP_204_NO_CONTENT)
async def detach_workspace_catalog(
    ws: str,
    catalog: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Detach a catalog from the workspace (it survives for other workspaces)."""
    workspace = await get_workspace(db, ws)
    if workspace is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    await assert_workspace_member(db, workspace.id, user.id, min_role="owner")
    cat = await resolve_catalog(db, workspace.id, catalog)
    await catalog_service.detach_catalog(db, workspace=workspace, catalog=cat)
    await db.commit()


@router.delete("/catalogs/{catalog_id}", status_code=status.HTTP_204_NO_CONTENT)
async def drop_catalog(
    catalog_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    polaris: PolarisClient = Depends(get_polaris_client),
) -> None:
    """Permanently delete a catalog. Allowed for its creator or an admin, and
    only when it is not attached to any workspace."""
    catalog = await db.get(Catalog, catalog_id)
    if catalog is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    if not await has_permission(db, user, Permission.CATALOGS_ADMIN) and (
        catalog.created_by != user.id
    ):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
    await catalog_service.drop_catalog(db, polaris, catalog=catalog)
    await db.commit()
