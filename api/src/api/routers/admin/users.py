import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_db, require_permission
from api.models.rbac import Role
from api.models.user import Credential, User
from api.schemas.auth import CreateUserRequest, UpdateUserRequest, UserOut
from api.services.auth import get_user_by_email, hash_password
from api.services.permissions import Permission

router = APIRouter()


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


@router.get("/users", response_model=list[UserOut])
async def list_users(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(Permission.USERS_MANAGE)),
) -> list[User]:
    result = await db.execute(select(User).order_by(User.created_at))
    return list(result.scalars().all())


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
