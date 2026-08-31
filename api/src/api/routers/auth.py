import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.config import settings
from api.deps import get_current_user, get_db, get_session_only_user
from api.models.user import Credential, User
from api.schemas.auth import AuthMethods, LoginRequest, OidcProviderInfo, UserOut
from api.schemas.service_account import PatTokenOut, SelfPatCreateRequest, SelfPatOut
from api.services.auth import (
    authenticate_password,
    create_session,
    delete_session,
    generate_pat,
    hash_token,
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


@me_router.post("/me/pats", response_model=PatTokenOut, status_code=status.HTTP_201_CREATED)
async def issue_own_pat(
    body: SelfPatCreateRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_session_only_user),
) -> PatTokenOut:
    """Issue a personal access token for yourself. The secret is shown once.

    This is how a person gets a credential for the CLI without an admin minting
    one for them. The token carries the caller's own identity, so it can do
    exactly what they can and nothing more -- including as their role changes,
    since permissions are resolved from the user at request time rather than
    frozen into the token.

    Reachable **only** with the browser session cookie, never with a bearer
    token: a PAT that could mint PATs would outlive its own revocation, because
    revoking the leaked one leaves every successor it issued working. Unattended
    callers use a service-account token instead, issued by an admin at
    `POST /admin/service-accounts/{service_account_id}/pats`.
    """
    token = generate_pat()
    expires_at = datetime.now(tz=UTC) + timedelta(days=body.expires_in_days)
    cred = Credential(
        user_id=user.id,
        kind="pat",
        token=None,
        token_hash=hash_token(token),
        expires_at=expires_at,
    )
    db.add(cred)
    await db.commit()
    await db.refresh(cred)
    return PatTokenOut(id=cred.id, token=token, expires_at=expires_at)


@me_router.get("/me/pats", response_model=list[SelfPatOut])
async def list_own_pats(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    authorization: str | None = Header(default=None, include_in_schema=False),
) -> list[SelfPatOut]:
    """Your own tokens: when each was issued, when it expires, and which is in use.

    **Never the secret.** Only a SHA-256 hash of a token is stored, so this cannot
    return one even in principle -- a token is shown once, at creation, and a
    forgotten one is replaced rather than recovered. That matches GitHub and
    GitLab, whose listings are likewise metadata-only.

    Because a hash identifies nothing a person can read, the token making this
    request is marked `current`. Without it a caller holding three tokens sees
    three indistinguishable rows and cannot tell which expiry is the one about to
    break them.
    """
    result = await db.execute(
        select(Credential)
        .where(Credential.user_id == user.id, Credential.kind == "pat")
        .order_by(Credential.created_at)
    )
    presented = (
        hash_token(authorization.removeprefix("Bearer ").strip())
        if authorization and authorization.startswith("Bearer ")
        else None
    )
    return [
        SelfPatOut(
            id=cred.id,
            created_at=cred.created_at,
            expires_at=cred.expires_at,
            current=presented is not None and cred.token_hash == presented,
        )
        for cred in result.scalars().all()
    ]


@me_router.delete("/me/pats/{pat_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_own_pat(
    pat_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> None:
    """Revoke one of your own tokens, including the one making this request.

    Unlike issuing, this accepts a bearer token as well as a session. Revoking
    only ever *removes* access, so a leaked token using it cannot escalate -- and
    a token being able to retire itself is worth more than the nuisance of one
    being used to retire its siblings. GitLab reaches the same conclusion, letting
    any token call its self-revocation route.

    Scoped to the caller's own credentials: another user's token is a 404 rather
    than a 403, so this cannot be used to discover that one exists.
    """
    result = await db.execute(
        select(Credential).where(
            Credential.id == pat_id,
            Credential.user_id == user.id,
            Credential.kind == "pat",
        )
    )
    cred = result.scalar_one_or_none()
    if cred is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    await db.delete(cred)
    await db.commit()
