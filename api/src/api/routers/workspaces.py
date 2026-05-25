import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_current_user, get_db, get_uc_client
from api.models.user import User
from api.models.workspace import Workspace, WorkspaceMember
from api.schemas.workspace import AddMemberRequest, MemberOut, WorkspaceCreate, WorkspaceOut
from api.services.unity_catalog import UCClient, UCError
from api.services.workspace import (
    assert_workspace_member,
    ensure_uc_catalog,
    get_workspace,
    mirror_member_grant,
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
    )
    return list(result.scalars().all())


@router.post("", response_model=WorkspaceOut, status_code=status.HTTP_201_CREATED)
async def create_workspace(
    body: WorkspaceCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    uc: UCClient = Depends(get_uc_client),
) -> Workspace:
    existing = await db.execute(select(Workspace).where(Workspace.slug == body.slug))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Slug already taken")
    ws = Workspace(
        slug=body.slug,
        name=body.name,
        storage_backend_id=body.storage_backend_id,
    )
    db.add(ws)
    await db.flush()
    member = WorkspaceMember(workspace_id=ws.id, user_id=user.id, role="owner")
    db.add(member)
    await db.commit()
    await db.refresh(ws)

    # Eagerly provision the workspace's UC catalog and default schema. If UC
    # is unreachable or otherwise fails, the pg row is rolled back so the
    # caller can retry once UC is healthy (D7).
    try:
        await ensure_uc_catalog(uc, ws.slug)
    except UCError as exc:
        logger.warning("UC provisioning failed for ws=%s; rolling back: %s", ws.slug, exc)
        await db.delete(member)
        await db.delete(ws)
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Unity Catalog provisioning failed: {exc}",
        ) from exc

    return ws


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
    uc: UCClient = Depends(get_uc_client),
) -> WorkspaceMember:
    workspace = await get_workspace(db, ws)
    if workspace is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    await assert_workspace_member(db, workspace.id, user.id, min_role="owner")
    member = WorkspaceMember(workspace_id=workspace.id, user_id=body.user_id, role=body.role)
    db.add(member)
    await db.commit()
    await db.refresh(member)

    # Defense-in-depth: mirror the grant to UC (best-effort, never blocks).
    target = await db.get(User, body.user_id)
    if target is not None:
        await mirror_member_grant(uc, workspace.slug, target.email, body.role)

    return member
