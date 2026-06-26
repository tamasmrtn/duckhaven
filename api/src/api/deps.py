from collections.abc import AsyncGenerator, Callable, Coroutine
from typing import Any

from fastapi import Cookie, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.db.session import async_session_factory
from api.models.user import User
from api.services.auth import get_session_user
from api.services.permissions import Permission
from api.services.polaris import PolarisClient
from api.services.rbac import has_permission


async def get_db() -> AsyncGenerator[AsyncSession]:
    async with async_session_factory() as session:
        yield session


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Yield the session factory itself so long-lived connections (e.g. the agent
    WebSocket) can open short-lived per-frame sessions instead of pinning one."""
    return async_session_factory


async def get_current_user(
    session: str | None = Cookie(default=None),
    db: AsyncSession = Depends(get_db),
) -> User:
    if not session:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    user = await get_session_user(db, session)
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    return user


def require_permission(
    permission: Permission,
) -> Callable[..., Coroutine[Any, Any, User]]:
    """Build a dependency that requires the current user to hold ``permission``.

    Replaces the old binary ``get_admin_user`` check: enforcement now flows
    through the role/permission model so a security reviewer sees exactly which
    capability each endpoint demands.
    """

    async def _require(
        user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ) -> User:
        if not await has_permission(db, user, permission):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
        return user

    return _require


async def get_polaris_client(request: Request) -> PolarisClient:
    """Return the process-wide PolarisClient owned by the app lifespan."""
    return request.app.state.polaris_client
