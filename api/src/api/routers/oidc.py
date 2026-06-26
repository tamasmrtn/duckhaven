"""OIDC SSO endpoints: redirect to the IdP and handle the callback.

The browser hits ``/auth/oidc/login`` (the "Sign in with SSO" button), is sent
to the IdP, and returns to ``/auth/oidc/callback`` with an authorization code.
On success we establish the *same* opaque DB session as a local login, so logout
and expiry behave identically.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import RedirectResponse

from api.config import settings
from api.deps import get_db
from api.services.auth import create_session, set_session_cookie
from api.services.oidc import OIDC_CLIENT_NAME, oauth
from api.services.provisioning import provision_federated_user, resolve_role

logger = logging.getLogger(__name__)

router = APIRouter()


def _callback_url(request: Request) -> str:
    base = settings.oidc_redirect_base_url or f"{request.url.scheme}://{request.url.netloc}"
    return f"{base.rstrip('/')}/api/auth/oidc/callback"


def _client():
    client = oauth.create_client(OIDC_CLIENT_NAME)
    if not settings.oidc_enabled or client is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="SSO is not enabled.")
    return client


@router.get("/login")
async def oidc_login(request: Request):
    """Kick off the Authorization Code + PKCE flow."""
    client = _client()
    return await client.authorize_redirect(request, _callback_url(request))


@router.get("/callback")
async def oidc_callback(request: Request, db: AsyncSession = Depends(get_db)):
    """Validate the IdP response, JIT-provision, and start a session."""
    client = _client()
    try:
        token = await client.authorize_access_token(request)
    except Exception:
        # Bad state, expired code, signature/nonce failure, or IdP unreachable.
        # Never leak token contents or stack traces to the browser.
        logger.warning("OIDC callback failed", exc_info=False)
        return RedirectResponse("/login?error=sso", status_code=status.HTTP_303_SEE_OTHER)

    claims = token.get("userinfo") or {}
    email = claims.get("email")
    sub = claims.get("sub")
    if not email or not sub:
        return RedirectResponse("/login?error=sso", status_code=status.HTTP_303_SEE_OTHER)

    groups = claims.get(settings.oidc_groups_claim) or []
    role = resolve_role(groups, settings.oidc_group_role_map)
    user = await provision_federated_user(
        db,
        email=email,
        name=claims.get("name") or email,
        subject=sub,
        provider="oidc",
        role=role,
    )

    session_token = await create_session(db, user.id)
    response = RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
    set_session_cookie(response, session_token)
    return response
