from collections.abc import AsyncGenerator

from fastapi import Cookie, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.session import async_session_factory
from api.models.user import User
from api.services.auth import get_session_user
from api.services.uc_credentials import CredCache
from api.services.unity_catalog import UCClient


async def get_db() -> AsyncGenerator[AsyncSession]:
    async with async_session_factory() as session:
        yield session


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


async def get_uc_client(request: Request) -> UCClient:
    """Return the process-wide UCClient owned by the app lifespan."""
    return request.app.state.uc_client


async def get_cred_cache(request: Request) -> CredCache:
    """Return the process-wide CredCache owned by the app lifespan."""
    return request.app.state.cred_cache
