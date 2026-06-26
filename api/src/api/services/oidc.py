"""OIDC client registration (Authlib).

A single ``OAuth`` registry holds the configured IdP. Registration is gated on
``oidc_enabled`` so the dependency is inert until an operator wires up an IdP.
Authlib handles discovery, Authorization Code + PKCE, and ID-token validation
(signature via JWKS, ``iss``/``aud``/``exp``, ``nonce``).
"""

from authlib.integrations.starlette_client import OAuth

from api.config import settings

OIDC_CLIENT_NAME = "duckhaven"

oauth = OAuth()


def register_oidc() -> None:
    """Register the IdP from settings. Idempotent; safe to call on each startup."""
    if not (settings.oidc_enabled and settings.oidc_server_metadata_url):
        return
    if oauth.create_client(OIDC_CLIENT_NAME) is not None:
        return
    oauth.register(
        name=OIDC_CLIENT_NAME,
        client_id=settings.oidc_client_id,
        client_secret=settings.oidc_client_secret,
        server_metadata_url=settings.oidc_server_metadata_url,
        client_kwargs={
            "scope": settings.oidc_scopes,
            "code_challenge_method": "S256",
        },
    )
