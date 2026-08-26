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
from api.models.catalog_grant import CatalogGrant
from api.models.storage_backend import StorageBackend
from api.models.user import User
from api.schemas.catalog_mgmt import CatalogAttachRequest, CatalogCreate, CatalogOut
from api.schemas.catalog_migration import (
    CatalogMigrationEventOut,
    CatalogMigrationOut,
    CatalogMigrationScalarOut,
    CatalogMigrationTableOut,
    MigrationStartRequest,
)
from api.services import catalog as catalog_service
from api.services.migration import service as migration_service
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


async def _catalog_for_admin(db: AsyncSession, user: User, catalog_id: uuid.UUID) -> Catalog:
    """Load a catalog, enforcing the same creator-or-admin gate as drop."""
    catalog = await db.get(Catalog, catalog_id)
    if catalog is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    if not await has_permission(db, user, Permission.CATALOGS_ADMIN) and (
        catalog.created_by != user.id
    ):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
    return catalog


def _migration_out(migration, *, include_tables: bool = False) -> CatalogMigrationOut:
    # Validated via the scalar-only schema first: the full schema declares
    # ``tables``, and pydantic's from_attributes mode reads every declared field,
    # which would lazy-load the relationship even when it isn't loaded/requested.
    scalars = CatalogMigrationScalarOut.model_validate(migration)
    out = CatalogMigrationOut(**scalars.model_dump())
    if include_tables:
        out.tables = [CatalogMigrationTableOut.model_validate(t) for t in migration.tables]
    return out


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
    catalog: Catalog,
    *,
    is_default: bool = False,
    attached_workspaces: int | None = None,
    access_mode: str = "open",
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
        access_mode=access_mode,
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
    """The catalogs attached to this workspace, with which one is the default.

    Catalogs are shared M:N, so one may be attached to several workspaces; the
    per-attachment access mode returned here is this workspace's, not the
    catalog's."""
    workspace = await get_workspace(db, ws)
    if workspace is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    await assert_workspace_member(db, workspace.id, user.id)
    catalogs = await resolve_workspace_catalogs(db, workspace.id)
    links = list(
        (
            await db.execute(
                select(WorkspaceCatalog).where(
                    WorkspaceCatalog.workspace_id == workspace.id,
                )
            )
        )
        .scalars()
        .all()
    )
    defaults = {link.catalog_id for link in links if link.is_default}
    modes = {link.catalog_id: link.access_mode for link in links}
    return [
        _catalog_out(
            c,
            is_default=c.id in defaults,
            attached_workspaces=await _binding_count(db, c.id),
            access_mode=modes.get(c.id, "open"),
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
        db,
        workspace=workspace,
        catalog=catalog,
        attached_by=user.id,
        access_mode=body.access_mode,
    )
    if body.access_mode == "scoped":
        # Seed the creator a catalog-level writer grant. Unlike per-agent access,
        # a scoped catalog has no bypass at all -- `grants.access_tier` returns
        # None without a covering grant no matter the workspace role, and the role
        # only caps. Creating one scoped would otherwise produce a catalog that
        # nobody, including the owner who just made it, can see or use.
        db.add(
            CatalogGrant(
                user_id=user.id,
                catalog_id=catalog.id,
                schema_name=None,
                table_name=None,
                tier="writer",
                created_by=user.id,
            )
        )
    await db.commit()
    await db.refresh(catalog, attribute_names=["storage_backend"])
    return _catalog_out(
        catalog,
        is_default=link.is_default,
        attached_workspaces=1,
        access_mode=link.access_mode,
    )


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


@router.post(
    "/catalogs/{catalog_id}/migrations",
    response_model=CatalogMigrationOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def start_catalog_migration(
    catalog_id: uuid.UUID,
    body: MigrationStartRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    polaris: PolarisClient = Depends(get_polaris_client),
) -> CatalogMigrationOut:
    """Begin migrating a catalog's Iceberg data to a new storage backend. The
    catalog goes read-only (writes rejected) until the background runner finishes
    and atomically cuts over. Allowed for the catalog's creator or an admin."""
    catalog = await _catalog_for_admin(db, user, catalog_id)
    target = await db.get(StorageBackend, body.target_storage_backend_id)
    if target is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Target storage backend not found"
        )
    migration = await migration_service.start_migration(
        db, polaris, catalog=catalog, target_backend=target, created_by=user.id
    )
    await db.commit()
    await db.refresh(migration)
    return _migration_out(migration)


@router.get("/catalogs/{catalog_id}/migrations", response_model=list[CatalogMigrationOut])
async def list_catalog_migrations(
    catalog_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[CatalogMigrationOut]:
    """Migration history for a catalog, newest first."""
    await _catalog_for_admin(db, user, catalog_id)
    migrations = await migration_service.list_migrations(db, catalog_id)
    return [_migration_out(m) for m in migrations]


@router.get("/catalogs/{catalog_id}/migrations/{migration_id}", response_model=CatalogMigrationOut)
async def get_catalog_migration(
    catalog_id: uuid.UUID,
    migration_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CatalogMigrationOut:
    """Status + progress for one migration, including per-table state."""
    await _catalog_for_admin(db, user, catalog_id)
    migration = await migration_service.get_migration(db, catalog_id, migration_id)
    await db.refresh(migration, attribute_names=["tables"])
    return _migration_out(migration, include_tables=True)


@router.get(
    "/catalogs/{catalog_id}/migrations/{migration_id}/logs",
    response_model=list[CatalogMigrationEventOut],
)
async def get_catalog_migration_logs(
    catalog_id: uuid.UUID,
    migration_id: uuid.UUID,
    after: int = 0,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[CatalogMigrationEventOut]:
    """User-facing log stream; ``after`` is the last seq the client has seen."""
    await _catalog_for_admin(db, user, catalog_id)
    await migration_service.get_migration(db, catalog_id, migration_id)
    events = await migration_service.list_events(db, migration_id, after=after)
    return [CatalogMigrationEventOut.model_validate(e) for e in events]


@router.post(
    "/catalogs/{catalog_id}/migrations/{migration_id}/cancel",
    response_model=CatalogMigrationOut,
)
async def cancel_catalog_migration(
    catalog_id: uuid.UUID,
    migration_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CatalogMigrationOut:
    """Request cancellation of an in-flight migration (only before cutover)."""
    await _catalog_for_admin(db, user, catalog_id)
    migration = await migration_service.get_migration(db, catalog_id, migration_id)
    await migration_service.request_cancel(db, migration)
    await db.commit()
    await db.refresh(migration)
    return _migration_out(migration)
