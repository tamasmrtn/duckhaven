import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_db, require_permission
from api.models.rbac import Role
from api.models.user import Credential, User
from api.models.workspace import Workspace, WorkspaceMember
from api.schemas.auth import CreateUserRequest, UpdateUserRequest, UserOut
from api.schemas.page import Page
from api.schemas.workspace import AdminUserWorkspace, SetMembershipRequest
from api.services.auth import get_user_by_email, hash_password
from api.services.paging import paginate
from api.services.permissions import Permission
from api.services.workspace import ROLE_ORDER, get_workspace

router = APIRouter()


async def _get_user_or_404(db: AsyncSession, user_id: uuid.UUID) -> User:
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return user


async def _assert_role_exists(db: AsyncSession, name: str) -> None:
    result = await db.execute(select(Role.id).where(Role.name == name))
    if result.scalar_one_or_none() is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"Unknown role: {name}"
        )


async def _active_admin_count(db: AsyncSession) -> int:
    result = await db.execute(
        select(func.count()).select_from(User).where(User.role == "admin", User.is_active.is_(True))
    )
    return result.scalar_one()


@router.get("/users", response_model=Page[UserOut])
async def list_users(
    limit: int = Query(default=100, ge=1, le=1000),
    cursor: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(Permission.USERS_MANAGE)),
) -> Page[UserOut]:
    """Every user account, oldest first, including deactivated ones.

    Service accounts are users too, so they appear here as well as under
    `/admin/service-accounts`; that endpoint is the one that narrows to them and
    reports their token counts."""
    rows, next_cursor, has_more = await paginate(
        db,
        select(User),
        sort=[User.created_at.asc(), User.id.asc()],
        limit=limit,
        cursor=cursor,
    )
    return Page[UserOut](
        items=[UserOut.model_validate(r[0], from_attributes=True) for r in rows],
        cursor=next_cursor,
        has_more=has_more,
    )


@router.post("/users", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def create_user(
    body: CreateUserRequest,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(Permission.USERS_MANAGE)),
) -> User:
    """Create a local (password) user. Federated users are provisioned on first
    SSO/LDAP login, not here."""
    await _assert_role_exists(db, body.role)
    if await get_user_by_email(db, body.email) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="A user with this email already exists."
        )
    user = User(
        email=body.email,
        name=body.name,
        password_hash=hash_password(body.password),
        role=body.role,
        auth_provider="local",
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


@router.patch("/users/{user_id}", response_model=UserOut)
async def update_user(
    user_id: uuid.UUID,
    body: UpdateUserRequest,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(Permission.USERS_MANAGE)),
) -> User:
    """Change a user's global role and/or active state. Guards against removing
    the last active admin so the system can never be locked out."""
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    demoting_admin = user.role == "admin" and body.role is not None and body.role != "admin"
    deactivating_admin = user.role == "admin" and body.is_active is False
    if (demoting_admin or deactivating_admin) and await _active_admin_count(db) <= 1:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cannot remove the last active admin.",
        )

    if body.role is not None:
        await _assert_role_exists(db, body.role)
        user.role = body.role
    if body.is_active is not None:
        user.is_active = body.is_active
    await db.commit()
    await db.refresh(user)
    return user


@router.post("/users/{user_id}/revoke-sessions", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_sessions(
    user_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(Permission.USERS_MANAGE)),
) -> None:
    """Force-log-out a user by deleting their active session credentials. Agent
    credentials are untouched (this filters on kind == "session")."""
    if await db.get(User, user_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    await db.execute(
        delete(Credential).where(Credential.user_id == user_id, Credential.kind == "session")
    )
    await db.commit()


# --- Workspace membership (admin-managed) -------------------------------------
# These let a users:manage admin grant any user access to any workspace, which
# the per-workspace endpoints can't (those require the caller to own the
# workspace). Workspace roles stay reader/writer/owner; the global role is
# separate.


@router.get("/users/{user_id}/workspaces", response_model=list[AdminUserWorkspace])
async def list_user_workspaces(
    user_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(Permission.USERS_MANAGE)),
) -> list[AdminUserWorkspace]:
    """Every workspace with the user's role in it (``None`` when not a member)."""
    await _get_user_or_404(db, user_id)
    result = await db.execute(
        select(Workspace, WorkspaceMember.role)
        .outerjoin(
            WorkspaceMember,
            (WorkspaceMember.workspace_id == Workspace.id) & (WorkspaceMember.user_id == user_id),
        )
        .order_by(Workspace.slug)
    )
    return [
        AdminUserWorkspace(workspace_id=ws.id, slug=ws.slug, name=ws.name, role=role)
        for ws, role in result.all()
    ]


@router.put("/users/{user_id}/workspaces/{workspace}", response_model=AdminUserWorkspace)
async def set_user_workspace_role(
    user_id: uuid.UUID,
    ws: Annotated[str, Path(alias="workspace")],
    body: SetMembershipRequest,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(Permission.USERS_MANAGE)),
) -> AdminUserWorkspace:
    """Add the user to a workspace or change their role there (idempotent)."""
    if body.role not in ROLE_ORDER:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"role must be one of {sorted(ROLE_ORDER)}",
        )
    await _get_user_or_404(db, user_id)
    workspace = await get_workspace(db, ws)
    if workspace is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    member = await db.get(WorkspaceMember, (workspace.id, user_id))
    if member is None:
        member = WorkspaceMember(workspace_id=workspace.id, user_id=user_id, role=body.role)
        db.add(member)
    else:
        member.role = body.role
    await db.commit()
    return AdminUserWorkspace(
        workspace_id=workspace.id, slug=workspace.slug, name=workspace.name, role=body.role
    )


@router.delete("/users/{user_id}/workspaces/{workspace}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_user_from_workspace(
    user_id: uuid.UUID,
    ws: Annotated[str, Path(alias="workspace")],
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(Permission.USERS_MANAGE)),
) -> None:
    """Remove the user from a workspace (no-op if they aren't a member)."""
    await _get_user_or_404(db, user_id)
    workspace = await get_workspace(db, ws)
    if workspace is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    await db.execute(
        delete(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace.id,
            WorkspaceMember.user_id == user_id,
        )
    )
    await db.commit()
