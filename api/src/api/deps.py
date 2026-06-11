from collections.abc import AsyncGenerator

from fastapi import Cookie, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.db.session import async_session_factory
from api.models.user import User
from api.services.auth import get_session_user
from api.services.polaris import PolarisClient


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
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    return user


async def get_admin_user(user: User = Depends(get_current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
    return user


async def get_polaris_client(request: Request) -> PolarisClient:
    """Return the process-wide PolarisClient owned by the app lifespan."""
    return request.app.state.polaris_client
