from collections.abc import AsyncGenerator, Callable, Coroutine
from typing import Any

from fastapi import Cookie, Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.db.session import async_session_factory
from api.models.user import User
from api.services.auth import get_pat_user, get_session_user
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
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Resolve the caller to a ``User``, from either an ``Authorization: Bearer``
    PAT (machine callers) or the ``session`` cookie (browser sessions).

    Bearer takes precedence when present. Both paths return the same ``User``
    type and feed the identical ``require_permission``/workspace enforcement — a
    service account is not a second authorization branch. ``is_active`` is
    enforced for both (the PAT path checks it inside ``get_pat_user``).
    """
    if authorization and authorization.startswith("Bearer "):
        user = await get_pat_user(db, authorization.removeprefix("Bearer ").strip())
        if user is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
        return user
    if session:
        user = await get_session_user(db, session)
        if user is not None and user.is_active:
            return user
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)


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
