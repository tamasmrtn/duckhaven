import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_current_user, get_db, get_polaris_client
from api.models.agent import Agent
from api.models.user import User
from api.models.workspace import Workspace, WorkspaceMember
from api.schemas.workspace import (
    AddMemberRequest,
    DefaultAgentOut,
    MemberOut,
    WorkspaceCreate,
    WorkspaceOut,
    WorkspaceUpdate,
)
from api.services.agent_access import assert_can_set_default_agent
from api.services.agent_dispatch import connected_agent_ids
from api.services.agent_view import effective_status
from api.services.polaris import PolarisClient
from api.services.runtimes import (
    AgentNotDispatchable,
    RuntimeRetired,
    assert_restartable,
    check_dispatchable,
)
from api.services.workspace import (
    UNSET,
    assert_workspace_member,
    get_default_catalog,
    mirror_member_grant,
    resolve_workspace_catalogs,
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
    default_agent = await _default_agent_out(db, workspace)
    return WorkspaceOut(
        id=workspace.id,
        slug=workspace.slug,
        name=workspace.name,
        description=workspace.description,
        created_at=workspace.created_at,
        default_catalog=default.slug if default is not None else None,
        storage_backend_id=default.storage_backend_id if default is not None else None,
        storage_backend_kind=(default.storage_backend.kind if default is not None else None),
        result_cache_enabled=workspace.result_cache_enabled,
        default_agent_id=workspace.default_agent_id,
        default_agent=default_agent,
    )


async def _default_agent_out(db: AsyncSession, workspace: Workspace) -> DefaultAgentOut | None:
    """The workspace's default compute, shown to every member.

    Not filtered by the caller's agent grants: members can use the default whether
    or not a grant reaches it, so hiding its name would hide the agent their
    queries run on."""
    if workspace.default_agent_id is None:
        return None
    agent = await db.get(Agent, workspace.default_agent_id)
    if agent is None:
        return None
    return DefaultAgentOut(
        id=agent.id,
        name=agent.name,
        provider=agent.provider,
        lifecycle=agent.lifecycle,
        status=effective_status(agent, await connected_agent_ids(db)),
    )


async def _validate_default_agent(
    db: AsyncSession, user: User, workspace: Workspace, agent_id: uuid.UUID | None
) -> None:
    """Refuse a default the workspace could never run on.

    The tier check comes first so a caller without access learns nothing about the
    agent. What is checked after it depends on what is knowable: a connected agent
    has reported its capabilities, so it is judged against the workspace's catalogs
    now. A stopped elastic agent has not, so only whether its runtime can still be
    started is checked; anything else surfaces when it comes back up."""
    agent = await assert_can_set_default_agent(db, user, agent_id)
    if agent is None:
        return
    if agent.capabilities:
        try:
            await check_dispatchable(agent, await resolve_workspace_catalogs(db, workspace.id))
        except AgentNotDispatchable as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail={"error": exc.code, "detail": exc.detail},
            ) from None
    if agent.provider is not None and agent.lifecycle in ("terminated", "failed"):
        try:
            assert_restartable(agent)
        except RuntimeRetired as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"error": "runtime_retired", "detail": str(exc)},
            ) from None


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
    """Rename a workspace, change its description, turn its result cache on or off, or
    set its default compute.

    A partial update: an omitted field is left alone, and an explicit ``null``
    description clears it. The slug is immutable -- it addresses the workspace."""
    workspace = await lookup_workspace(db, ws)
    if workspace is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    # Identity edits are gated at the same tier as membership management.
    await assert_workspace_member(db, workspace.id, user.id, min_role="owner")
    description = body.description if "description" in body.model_fields_set else UNSET
    default_agent_id = UNSET
    if "default_agent_id" in body.model_fields_set:
        default_agent_id = body.default_agent_id
        # An unchanged value is not re-validated: re-saving the page must not fail
        # because the setter's own access has since changed.
        if default_agent_id != workspace.default_agent_id:
            await _validate_default_agent(db, user, workspace, default_agent_id)
            logger.info(
                "workspace_default_agent_changed workspace=%s actor=%s old=%s new=%s",
                workspace.slug,
                user.id,
                workspace.default_agent_id,
                default_agent_id,
            )
    workspace = await apply_workspace_update(
        db,
        workspace,
        name=body.name,
        description=description,
        result_cache_enabled=body.result_cache_enabled,
        default_agent_id=default_agent_id,
    )
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
