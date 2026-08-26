from fastapi import APIRouter, Cookie, Depends, HTTPException, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from api.config import settings
from api.deps import get_current_user, get_db
from api.models.user import User
from api.schemas.auth import AuthMethods, LoginRequest, OidcProviderInfo, UserOut
from api.services.auth import (
    authenticate_password,
    create_session,
    delete_session,
    set_session_cookie,
)
from api.services.rbac import user_permissions

router = APIRouter()
me_router = APIRouter()


async def _user_out_with_permissions(db: AsyncSession, user: User) -> UserOut:
    out = UserOut.model_validate(user)
    out.permissions = sorted(await user_permissions(db, user))
    return out


@router.get("/methods", response_model=AuthMethods)
async def methods() -> AuthMethods:
    """Tell the SPA which login methods to render. Local is always available so
    the break-glass admin can sign in regardless of IdP state."""
    return AuthMethods(
        local=True,
        ldap=settings.ldap_enabled,
        oidc_providers=[
            OidcProviderInfo(id=p.id, label=p.label) for p in settings.effective_oidc_providers()
        ],
    )


@router.post("/login", response_model=UserOut)
async def login(
    body: LoginRequest, response: Response, db: AsyncSession = Depends(get_db)
) -> UserOut:
    """Sign in with email and password, setting the `session` cookie.

    Returns the user with their resolved permissions, so the SPA can render the
    right navigation without a second request. Machine callers should use a
    service-account token on the `Authorization` header instead."""
    user = await authenticate_password(db, body.email, body.password)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    token = await create_session(db, user.id)
    set_session_cookie(response, token)
    return await _user_out_with_permissions(db, user)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    response: Response,
    session: str | None = Cookie(default=None, include_in_schema=False),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Sign out: revoke the session server-side and clear the cookie.

    Succeeds whether or not a session was presented -- signing out of nothing is
    not an error."""
    if session:
        await delete_session(db, session)
    response.delete_cookie("session")


@me_router.get("/me", response_model=UserOut)
async def me(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> UserOut:
    """The authenticated caller, with their resolved permissions.

    Resolves either credential, so a service account sees the identity its token
    maps to."""
    return await _user_out_with_permissions(db, user)
