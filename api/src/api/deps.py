import uuid
from collections.abc import AsyncGenerator, Callable, Coroutine
from typing import Any

from fastapi import Cookie, Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.db.session import async_session_factory
from api.models.agent import Agent
from api.models.user import User
from api.services.agent_access import ResolvedAgent, assert_agent_tier
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


def _bearer(authorization: str | None) -> str | None:
    """The token from an ``Authorization: Bearer`` header, or None.

    RFC 9110 makes the scheme case-insensitive, and matching it case-sensitively
    was not merely pedantic: a lowercase ``bearer`` fell through to the cookie
    branch, so a caller sending both got the request they should have been
    refused, and one sending only a token got a bare 401 instead of the message
    telling them what to use.
    """
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    return token.strip() if scheme.lower() == "bearer" else None


async def get_current_user(
    session: str | None = Cookie(default=None, include_in_schema=False),
    authorization: str | None = Header(default=None, include_in_schema=False),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Resolve the caller to a ``User``, from either an ``Authorization: Bearer``
    PAT (machine callers) or the ``session`` cookie (browser sessions).

    Bearer takes precedence when present. Both paths return the same ``User``
    type and feed the identical ``require_permission``/workspace enforcement — a
    service account is not a second authorization branch. ``is_active`` is
    enforced for both (the PAT path checks it inside ``get_pat_user``).
    """
    if _bearer(authorization) is not None:
        user = await get_pat_user(db, _bearer(authorization))
        if user is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
        return user
    if session:
        user = await get_session_user(db, session)
        if user is not None and user.is_active:
            return user
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)


async def get_session_only_user(
    session: str | None = Cookie(default=None, include_in_schema=False),
    authorization: str | None = Header(default=None, include_in_schema=False),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Resolve the caller from the ``session`` cookie alone, refusing a Bearer PAT.

    The counterpart to :func:`get_current_user` for the one operation that must
    not accept a token: minting a PAT. A token able to mint tokens outlives its
    own revocation -- revoke the leaked one and the successors it issued keep
    working -- so creating one always costs an interactive sign-in.

    A presented Bearer token is refused with 403 rather than ignored, so an
    unattended caller is told to use a service-account token instead of reading a
    bare 401 as a bad credential.
    """
    if _bearer(authorization) is not None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": "session_required",
                "detail": (
                    "Issuing a token requires an interactive session, not a bearer token. "
                    "For unattended callers, issue a service-account token instead."
                ),
            },
        )
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


def require_agent_tier(
    min_tier: str,
) -> Callable[..., Coroutine[Any, Any, ResolvedAgent]]:
    """Build a dependency resolving the route's ``agent_id`` and requiring ``min_tier``.

    The per-agent counterpart to ``require_permission``: the global permission says
    whether a caller may manage agents at all, this says which agent. Global
    ``agents:manage`` always satisfies it (see ``services.agent_access``).

    404 when the caller cannot see the agent, 403 when they can but lack the tier.
    Returns the agent *and* the caller's actual tier, so a handler can echo it back
    in ``AgentOut`` without re-resolving.

    Declared as a dependency rather than a call inside each handler so the guard is
    visible in the route signature — and so it cannot be forgotten when a new
    ``/{agent_id}/...`` route is added.
    """

    async def _dep(
        agent_id: uuid.UUID,
        user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ) -> ResolvedAgent:
        agent = await db.get(Agent, agent_id)
        if agent is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")
        tier = await assert_agent_tier(db, user, agent, min_tier)
        return ResolvedAgent(agent=agent, tier=tier)

    return _dep


async def get_polaris_client(request: Request) -> PolarisClient:
    """Return the process-wide PolarisClient owned by the app lifespan."""
    return request.app.state.polaris_client
