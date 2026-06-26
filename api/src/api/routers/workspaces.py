import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from api.deps import get_current_user, get_db, get_polaris_client
from api.models.storage_backend import StorageBackend
from api.models.user import User
from api.models.workspace import Workspace, WorkspaceMember
from api.schemas.workspace import AddMemberRequest, MemberOut, WorkspaceCreate, WorkspaceOut
from api.services.polaris import PolarisClient, PolarisError
from api.services.workspace import (
    assert_workspace_member,
    default_object_store_backend,
    ensure_polaris_catalog,
    get_workspace,
    mirror_member_grant,
    polaris_storage,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/workspaces")


@router.get("", response_model=list[WorkspaceOut])
async def list_workspaces(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[Workspace]:
    result = await db.execute(
        select(Workspace)
        .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
        .where(WorkspaceMember.user_id == user.id)
        .options(selectinload(Workspace.storage_backend))
    )
    return list(result.scalars().all())


@router.post("", response_model=WorkspaceOut, status_code=status.HTTP_201_CREATED)
async def create_workspace(
    body: WorkspaceCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    polaris: PolarisClient = Depends(get_polaris_client),
) -> Workspace:
    existing = await db.execute(select(Workspace).where(Workspace.slug == body.slug))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Slug already taken")
    # No backend chosen → auto-provision a bundled object-store backend so the
    # common case needs only a workspace name. Track it so it can be rolled back
    # alongside the workspace if Polaris provisioning fails below.
    auto_backend: StorageBackend | None = None
    if body.storage_backend_id is None:
        backend = default_object_store_backend(body.name, user.id)
        db.add(backend)
        await db.flush()
        auto_backend = backend
    else:
        backend = await db.get(StorageBackend, body.storage_backend_id)
        if backend is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Unknown storage backend"
            )
    ws = Workspace(
        slug=body.slug,
        name=body.name,
        storage_backend_id=backend.id,
    )
    db.add(ws)
    await db.flush()
    member = WorkspaceMember(workspace_id=ws.id, user_id=user.id, role="owner")
    db.add(member)
    await db.commit()
    await db.refresh(ws)

    # Eagerly provision the workspace's Polaris catalog and default namespace.
    # If Polaris is unreachable or otherwise fails, the pg row is rolled back
    # so the caller can retry once Polaris is healthy (D7).
    storage_type, base_location, extra_storage = polaris_storage(backend.kind, backend.root_uri)
    try:
        await ensure_polaris_catalog(
            polaris,
            ws.slug,
            storage_type=storage_type,
            base_location=base_location,
            extra_storage=extra_storage,
        )
    except PolarisError as exc:
        logger.warning("Polaris provisioning failed for ws=%s; rolling back: %s", ws.slug, exc)
        await db.delete(member)
        await db.delete(ws)
        if auto_backend is not None:
            await db.delete(auto_backend)
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Polaris provisioning failed: {exc}",
        ) from exc

    # Reload with the storage backend so WorkspaceOut can expose its kind.
    result = await db.execute(
        select(Workspace)
        .options(selectinload(Workspace.storage_backend))
        .where(Workspace.id == ws.id)
    )
    return result.scalar_one()


@router.get("/{ws}", response_model=WorkspaceOut)
async def get_workspace_detail(
    ws: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Workspace:
    workspace = await get_workspace(db, ws)
    if workspace is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    await assert_workspace_member(db, workspace.id, user.id)
    return workspace


@router.get("/{ws}/members", response_model=list[MemberOut])
async def list_members(
    ws: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[WorkspaceMember]:
    workspace = await get_workspace(db, ws)
    if workspace is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    await assert_workspace_member(db, workspace.id, user.id, min_role="owner")
    result = await db.execute(
        select(WorkspaceMember).where(WorkspaceMember.workspace_id == workspace.id)
    )
    return list(result.scalars().all())


@router.post("/{ws}/members", response_model=MemberOut, status_code=status.HTTP_201_CREATED)
async def add_member(
    ws: str,
    body: AddMemberRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    polaris: PolarisClient = Depends(get_polaris_client),
) -> WorkspaceMember:
    workspace = await get_workspace(db, ws)
    if workspace is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    await assert_workspace_member(db, workspace.id, user.id, min_role="owner")
    member = WorkspaceMember(workspace_id=workspace.id, user_id=body.user_id, role=body.role)
    db.add(member)
    await db.commit()
    await db.refresh(member)

    # Permission authority is the API boundary (D10); the catalog grant mirror
    # is a no-op (see services/workspace.mirror_member_grant).
    target = await db.get(User, body.user_id)
    if target is not None:
        await mirror_member_grant(polaris, workspace.slug, target.email, body.role)

    return member
