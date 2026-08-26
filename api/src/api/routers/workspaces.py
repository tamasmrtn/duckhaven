import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_current_user, get_db, get_polaris_client
from api.models.user import User
from api.models.workspace import Workspace, WorkspaceMember
from api.schemas.workspace import (
    AddMemberRequest,
    MemberOut,
    WorkspaceCreate,
    WorkspaceOut,
    WorkspaceUpdate,
)
from api.services.polaris import PolarisClient
from api.services.workspace import (
    UNSET,
    assert_workspace_member,
    get_default_catalog,
    mirror_member_grant,
)

# Aliased: the route handlers are named for the operations they expose, which
# collides with the service functions they call.
from api.services.workspace import delete_workspace as remove_workspace
from api.services.workspace import get_workspace as lookup_workspace
from api.services.workspace import update_workspace as apply_workspace_update

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
        description=workspace.description,
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
    """The workspaces the caller is a member of, with each one's default catalog.

    Membership is the filter: this is not an admin listing, and a workspace the
    caller has no role in is invisible rather than forbidden."""
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
    await db.commit()
    await db.refresh(ws)
    return await _workspace_out(db, ws)


@router.get("/{workspace}", response_model=WorkspaceOut)
async def get_workspace(
    ws: Annotated[str, Path(alias="workspace")],
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> WorkspaceOut:
    """One workspace, in the same shape the list returns.

    ``ws`` accepts the slug or the id: a slug is what a person types, an id is
    what a stored reference holds."""
    workspace = await lookup_workspace(db, ws)
    if workspace is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    await assert_workspace_member(db, workspace.id, user.id)
    return await _workspace_out(db, workspace)


@router.patch("/{workspace}", response_model=WorkspaceOut)
async def update_workspace(
    ws: Annotated[str, Path(alias="workspace")],
    body: WorkspaceUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> WorkspaceOut:
    """Rename a workspace or change its description.

    A partial update: an omitted field is left alone, and an explicit ``null``
    description clears it. The slug is immutable -- it addresses the workspace."""
    workspace = await lookup_workspace(db, ws)
    if workspace is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    # Identity edits are gated at the same tier as membership management.
    await assert_workspace_member(db, workspace.id, user.id, min_role="owner")
    description = body.description if "description" in body.model_fields_set else UNSET
    workspace = await apply_workspace_update(db, workspace, name=body.name, description=description)
    return await _workspace_out(db, workspace)


@router.delete("/{workspace}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_workspace(
    ws: Annotated[str, Path(alias="workspace")],
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Delete a workspace and everything scoped to it.

    Owner only. Catalogs survive: they are attached M:N and may be bound to other
    workspaces, so this removes the bindings, not the data."""
    workspace = await lookup_workspace(db, ws)
    if workspace is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    await assert_workspace_member(db, workspace.id, user.id, min_role="owner")
    await remove_workspace(db, workspace)


@router.get("/{workspace}/members", response_model=list[MemberOut])
async def list_members(
    ws: Annotated[str, Path(alias="workspace")],
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[WorkspaceMember]:
    """Everyone with a role in the workspace. Owner only -- the membership list
    is also the access-control list."""
    workspace = await lookup_workspace(db, ws)
    if workspace is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    await assert_workspace_member(db, workspace.id, user.id, min_role="owner")
    result = await db.execute(
        select(WorkspaceMember).where(WorkspaceMember.workspace_id == workspace.id)
    )
    return list(result.scalars().all())


@router.post("/{workspace}/members", response_model=MemberOut, status_code=status.HTTP_201_CREATED)
async def add_member(
    ws: Annotated[str, Path(alias="workspace")],
    body: AddMemberRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    polaris: PolarisClient = Depends(get_polaris_client),
) -> WorkspaceMember:
    """Give an existing user a role in the workspace.

    Roles are ordered reader < writer < owner; the role granted here is the floor
    every workspace-scoped permission check measures against."""
    workspace = await lookup_workspace(db, ws)
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
