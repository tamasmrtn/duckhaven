"""OIDC client registration (Authlib), one client per configured provider.

A single ``OAuth`` registry holds every configured IdP, registered under a
per-provider client name. Registration reads ``Settings.effective_oidc_providers``
so the registry is inert until an operator configures at least one provider.
Authlib handles discovery, Authorization Code + PKCE, and ID-token validation
(signature via JWKS, ``iss``/``aud``/``exp``, ``nonce``).
"""

from authlib.integrations.starlette_client import OAuth

from api.config import OidcProvider, settings

oauth = OAuth()


def client_name(provider_id: str) -> str:
    """Authlib client name for a provider id (namespaced to avoid collisions)."""
    return f"oidc_{provider_id}"


def get_provider(provider_id: str) -> OidcProvider | None:
    """The configured provider with this id, or None."""
    return next((p for p in settings.effective_oidc_providers() if p.id == provider_id), None)


def register_oidc() -> None:
    """Register every configured provider. Idempotent; safe to call per startup."""
    for provider in settings.effective_oidc_providers():
        name = client_name(provider.id)
        if oauth.create_client(name) is not None:
            continue
        oauth.register(
            name=name,
            client_id=provider.client_id,
            client_secret=provider.client_secret,
            server_metadata_url=provider.server_metadata_url,
            client_kwargs={
                "scope": provider.scopes,
                "code_challenge_method": "S256",
            },
        )


def reset_oidc_clients() -> None:
    """Forget all registered clients so ``register_oidc`` re-reads settings.

    Used by tests that swap the provider configuration between cases."""
    for attr in ("_clients", "_registry"):
        registry = getattr(oauth, attr, None)
        if isinstance(registry, dict):
            registry.clear()
