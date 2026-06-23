import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_current_user, get_db, get_polaris_client
from api.models.user import User
from api.models.workspace import Workspace, WorkspaceMember
from api.schemas.workspace import AddMemberRequest, MemberOut, WorkspaceCreate, WorkspaceOut
from api.services.polaris import PolarisClient
from api.services.system_catalog.bootstrap import link_system_catalog
from api.services.workspace import (
    assert_workspace_member,
    get_default_catalog,
    get_workspace,
    mirror_member_grant,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/workspaces")


async def _workspace_out(db: AsyncSession, workspace: Workspace) -> WorkspaceOut:
    """Serialize a workspace, summarizing its default catalog's storage so the
    UI's backend badge keeps rendering after storage moved onto catalogs."""
    default = await get_default_catalog(db, workspace.id)
    return WorkspaceOut(
        id=workspace.id,
        slug=workspace.slug,
        name=workspace.name,
        created_at=workspace.created_at,
        default_catalog=default.slug if default is not None else None,
        storage_backend_id=default.storage_backend_id if default is not None else None,
        storage_backend_kind=(default.storage_backend.kind if default is not None else None),
    )


@router.get("", response_model=list[WorkspaceOut])
async def list_workspaces(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[WorkspaceOut]:
    result = await db.execute(
        select(Workspace)
        .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
        .where(WorkspaceMember.user_id == user.id)
    )
    return [await _workspace_out(db, ws) for ws in result.scalars().all()]


@router.post("", response_model=WorkspaceOut, status_code=status.HTTP_201_CREATED)
async def create_workspace(
    body: WorkspaceCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> WorkspaceOut:
    """Create a workspace with its owner. A workspace starts with **no catalog** —
    the owner creates or attaches catalogs afterward (catalogs are decoupled,
    M:N). No storage backend or Polaris provisioning happens here."""
    existing = await db.execute(select(Workspace).where(Workspace.slug == body.slug))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Slug already taken")
    ws = Workspace(slug=body.slug, name=body.name)
    db.add(ws)
    await db.flush()
    member = WorkspaceMember(workspace_id=ws.id, user_id=user.id, role="owner")
    db.add(member)
    # The built-in system catalog is attached to every workspace by default
    # (no-op until it has been provisioned during admin setup).
    await link_system_catalog(db, ws.id)
    await db.commit()
    await db.refresh(ws)
    return await _workspace_out(db, ws)


@router.get("/{ws}", response_model=WorkspaceOut)
async def get_workspace_detail(
    ws: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> WorkspaceOut:
    workspace = await get_workspace(db, ws)
    if workspace is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    await assert_workspace_member(db, workspace.id, user.id)
    return await _workspace_out(db, workspace)


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
