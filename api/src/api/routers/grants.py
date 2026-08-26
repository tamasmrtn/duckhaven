"""Manage scoped access: a catalog attachment's access mode and its grants.

Owner-gated (mirrors how workspace membership is managed), keyed by
``(workspace, catalog)`` — the principal picker is the workspace's members, and
the common case is one catalog attached to one workspace. Grant rows are stored
per catalog, so they apply wherever the catalog is attached in scoped mode.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Response, status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_current_user, get_db
from api.models.catalog import Catalog, WorkspaceCatalog
from api.models.catalog_grant import CatalogGrant
from api.models.user import User
from api.models.workspace import WorkspaceMember
from api.routers.admin.service_accounts import SERVICE_ACCOUNT_PROVIDER
from api.schemas.grant import (
    AccessModeUpdate,
    CatalogGrantsOut,
    GrantOut,
    GrantPrincipalOut,
    GrantUpsert,
)
from api.services.workspace import assert_workspace_member, get_workspace, resolve_catalog

router = APIRouter()


async def _payload(db: AsyncSession, workspace_id: uuid.UUID, catalog: Catalog) -> CatalogGrantsOut:
    mode = (
        await db.execute(
            select(WorkspaceCatalog.access_mode).where(
                WorkspaceCatalog.workspace_id == workspace_id,
                WorkspaceCatalog.catalog_id == catalog.id,
            )
        )
    ).scalar_one_or_none() or "open"

    grant_rows = (
        await db.execute(
            select(CatalogGrant, User.name)
            .outerjoin(User, CatalogGrant.user_id == User.id)
            .where(CatalogGrant.catalog_id == catalog.id)
            .order_by(CatalogGrant.schema_name, CatalogGrant.table_name)
        )
    ).all()
    grants = [
        GrantOut.model_validate(g).model_copy(update={"user_name": name}) for g, name in grant_rows
    ]

    member_rows = (
        await db.execute(
            select(WorkspaceMember.role, User)
            .join(User, WorkspaceMember.user_id == User.id)
            .where(WorkspaceMember.workspace_id == workspace_id)
            .order_by(User.name)
        )
    ).all()
    principals = [
        GrantPrincipalOut(
            user_id=u.id,
            name=u.name,
            email=u.email,
            role=role,
            is_service_account=u.auth_provider == SERVICE_ACCOUNT_PROVIDER,
        )
        for role, u in member_rows
    ]
    return CatalogGrantsOut(access_mode=mode, grants=grants, principals=principals)


@router.get("/workspaces/{workspace}/catalogs/{catalog}/grants", response_model=CatalogGrantsOut)
async def list_catalog_grants(
    ws: Annotated[str, Path(alias="workspace")],
    catalog: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CatalogGrantsOut:
    """The catalog's access mode and every grant on it. Owner only.

    Returns the grants together with the principals they name, so the access
    screen renders from one request."""
    workspace = await get_workspace(db, ws)
    if workspace is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    await assert_workspace_member(db, workspace.id, user.id, min_role="owner")
    cat = await resolve_catalog(db, workspace.id, catalog)
    return await _payload(db, workspace.id, cat)


@router.patch(
    "/workspaces/{workspace}/catalogs/{catalog}/access-mode", response_model=CatalogGrantsOut
)
async def set_access_mode(
    ws: Annotated[str, Path(alias="workspace")],
    catalog: str,
    body: AccessModeUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CatalogGrantsOut:
    """Toggle a catalog attachment between ``open`` (workspace role governs) and
    ``scoped`` (grants govern). Affects only this workspace's attachment."""
    workspace = await get_workspace(db, ws)
    if workspace is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    await assert_workspace_member(db, workspace.id, user.id, min_role="owner")
    cat = await resolve_catalog(db, workspace.id, catalog)
    await db.execute(
        update(WorkspaceCatalog)
        .where(
            WorkspaceCatalog.workspace_id == workspace.id,
            WorkspaceCatalog.catalog_id == cat.id,
        )
        .values(access_mode=body.access_mode)
    )
    await db.commit()
    return await _payload(db, workspace.id, cat)


@router.put(
    "/workspaces/{workspace}/catalogs/{catalog}/grants",
    response_model=GrantOut,
    responses={
        200: {"description": "The principal already had a grant here; its tier was replaced."},
        201: {"description": "A new grant was created for this principal."},
    },
)
async def upsert_grant(
    ws: Annotated[str, Path(alias="workspace")],
    catalog: str,
    body: GrantUpsert,
    response: Response,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> GrantOut:
    """Create or update a grant for a member at a catalog/schema/table node."""
    workspace = await get_workspace(db, ws)
    if workspace is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    await assert_workspace_member(db, workspace.id, user.id, min_role="owner")
    cat = await resolve_catalog(db, workspace.id, catalog)

    # A grant only makes sense for a member of this workspace.
    target = (
        await db.execute(
            select(WorkspaceMember).where(
                WorkspaceMember.workspace_id == workspace.id,
                WorkspaceMember.user_id == body.user_id,
            )
        )
    ).scalar_one_or_none()
    if target is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Principal is not a member of this workspace.",
        )

    existing = (
        await db.execute(
            select(CatalogGrant).where(
                CatalogGrant.user_id == body.user_id,
                CatalogGrant.catalog_id == cat.id,
                CatalogGrant.schema_name.is_(body.schema_name)
                if body.schema_name is None
                else CatalogGrant.schema_name == body.schema_name,
                CatalogGrant.table_name.is_(body.table_name)
                if body.table_name is None
                else CatalogGrant.table_name == body.table_name,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        existing.tier = body.tier
        grant = existing
    else:
        grant = CatalogGrant(
            user_id=body.user_id,
            catalog_id=cat.id,
            schema_name=body.schema_name,
            table_name=body.table_name,
            tier=body.tier,
            created_by=user.id,
        )
        db.add(grant)
        response.status_code = status.HTTP_201_CREATED
    await db.commit()
    await db.refresh(grant)
    name = (
        await db.execute(select(User.name).where(User.id == grant.user_id))
    ).scalar_one_or_none()
    return GrantOut.model_validate(grant).model_copy(update={"user_name": name})


@router.delete(
    "/workspaces/{workspace}/catalogs/{catalog}/grants/{grant_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_grant(
    ws: Annotated[str, Path(alias="workspace")],
    catalog: str,
    grant_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Revoke one grant. Owner only.

    What the principal can still reach depends on the catalog's access mode: in
    `open` mode they keep workspace-level access, in `restricted` mode removing
    their last grant removes their access."""
    workspace = await get_workspace(db, ws)
    if workspace is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    await assert_workspace_member(db, workspace.id, user.id, min_role="owner")
    cat = await resolve_catalog(db, workspace.id, catalog)
    grant = (
        await db.execute(
            select(CatalogGrant).where(
                CatalogGrant.id == grant_id, CatalogGrant.catalog_id == cat.id
            )
        )
    ).scalar_one_or_none()
    if grant is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    await db.delete(grant)
    await db.commit()
