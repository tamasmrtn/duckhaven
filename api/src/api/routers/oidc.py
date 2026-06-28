"""OIDC SSO endpoints: redirect to the IdP and handle the callback.

The browser hits ``/auth/oidc/{provider}/login`` (a "Sign in with …" button), is
sent to that provider's IdP, and returns to ``/auth/oidc/{provider}/callback``
with an authorization code. On success we establish the *same* opaque DB session
as a local login, so logout and expiry behave identically. Multiple providers may
be configured; each is addressed by its url-safe ``id``.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import RedirectResponse

from api.config import OidcProvider, settings
from api.deps import get_db
from api.services.auth import create_session, set_session_cookie
from api.services.oidc import client_name, get_provider, oauth
from api.services.provisioning import provision_federated_user, resolve_role

logger = logging.getLogger(__name__)

router = APIRouter()


def _callback_url(request: Request, provider_id: str) -> str:
    base = settings.oidc_redirect_base_url or f"{request.url.scheme}://{request.url.netloc}"
    return f"{base.rstrip('/')}/api/auth/oidc/{provider_id}/callback"


def _resolve(provider_id: str) -> tuple[OidcProvider, object]:
    """Look up a configured provider + its Authlib client, or 404."""
    provider = get_provider(provider_id)
    client = oauth.create_client(client_name(provider_id)) if provider else None
    if provider is None or client is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="SSO provider not found.")
    return provider, client


@router.get("/{provider}/login")
async def oidc_login(provider: str, request: Request):
    """Kick off the Authorization Code + PKCE flow for one provider."""
    _prov, client = _resolve(provider)
    return await client.authorize_redirect(request, _callback_url(request, provider))


@router.get("/{provider}/callback")
async def oidc_callback(provider: str, request: Request, db: AsyncSession = Depends(get_db)):
    """Validate the IdP response, JIT-provision, and start a session."""
    prov, client = _resolve(provider)
    try:
        token = await client.authorize_access_token(request)
    except Exception:  # noqa: BLE001 - any failure must fall back to local login
        # Bad state, expired code, signature/nonce failure, or IdP unreachable.
        # Never leak token contents or stack traces to the browser.
        logger.warning("OIDC callback failed for provider=%s", provider, exc_info=False)
        return RedirectResponse("/login?error=sso", status_code=status.HTTP_303_SEE_OTHER)

    claims = token.get("userinfo") or {}
    # Providers differ on where the address lands: the OIDC standard `email`
    # claim, but Entra ID commonly omits it and carries the UPN in
    # `preferred_username` (some IdPs use `upn`). Fall back so a directory
    # without a mailbox-backed `email` claim still resolves an identity.
    email = claims.get("email") or claims.get("preferred_username") or claims.get("upn")
    sub = claims.get("sub")
    if not email or not sub:
        # Log the claim names (never values) so a misconfigured IdP is
        # diagnosable instead of silently bouncing to /login?error=sso.
        logger.warning(
            "OIDC callback missing identity claims for provider=%s (present: %s)",
            provider,
            sorted(claims),
        )
        return RedirectResponse("/login?error=sso", status_code=status.HTTP_303_SEE_OTHER)

    groups = claims.get(prov.groups_claim) or []
    role = resolve_role(groups, prov.group_role_map)
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
