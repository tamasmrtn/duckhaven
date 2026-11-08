import api.routers.oidc as oidc_mod
from api.config import OidcProvider, settings
from api.services.auth import get_user_by_email


class _TokenClient:
    """Stand-in for the Authlib client: returns pre-baked ID-token claims."""

    def __init__(self, claims: dict):
        self._claims = claims

    async def authorize_access_token(self, request):
        return {"userinfo": self._claims}


def _provider(group_role_map: dict[str, str] | None = None, *, id: str = "entra") -> OidcProvider:
    return OidcProvider(
        id=id,
        label="Microsoft",
        server_metadata_url="https://idp.test/.well-known/openid-configuration",
        client_id="cid",
        client_secret="sec",
        group_role_map=group_role_map or {},
    )


async def test_callback_provisions_user_and_starts_session(client, db_session, monkeypatch):
    monkeypatch.setattr(settings, "oidc_providers", [_provider({"admins": "admin"})])
    fake = _TokenClient(
        {"email": "eve@corp.com", "sub": "sub-9", "name": "Eve", "groups": ["admins"]}
    )
    monkeypatch.setattr(oidc_mod.oauth, "create_client", lambda name: fake)

    resp = await client.get("/auth/oidc/entra/callback", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/"
    assert "session" in resp.cookies

    eve = await get_user_by_email(db_session, "eve@corp.com")
    assert eve is not None
    assert eve.auth_provider == "oidc:entra"
    assert eve.role == "admin"
    assert eve.password_hash is None


async def test_callback_falls_back_to_preferred_username(client, db_session, monkeypatch):
    """Entra omits the `email` claim; the UPN in `preferred_username` is used."""
    monkeypatch.setattr(settings, "oidc_providers", [_provider()])
    fake = _TokenClient({"preferred_username": "bob@corp.com", "sub": "sub-1", "name": "Bob"})
    monkeypatch.setattr(oidc_mod.oauth, "create_client", lambda name: fake)

    resp = await client.get("/auth/oidc/entra/callback", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/"
    bob = await get_user_by_email(db_session, "bob@corp.com")
    assert bob is not None and bob.role == "user"


async def test_a_second_provider_cannot_sign_in_to_an_account(client, db_session, monkeypatch):
    """The same email from another configured provider is refused, not bound."""
    monkeypatch.setattr(settings, "oidc_providers", [_provider(), _provider(id="google")])
    claims = {"email": "eve@corp.com", "sub": "sub-9", "name": "Eve"}
    monkeypatch.setattr(oidc_mod.oauth, "create_client", lambda name: _TokenClient(claims))
    first = await client.get("/auth/oidc/entra/callback", follow_redirects=False)
    assert first.headers["location"] == "/"
    client.cookies.clear()

    claims.update(sub="other-sub", email_verified=True)
    resp = await client.get("/auth/oidc/google/callback", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login?error=sso"
    assert "session" not in resp.cookies

    eve = await get_user_by_email(db_session, "eve@corp.com")
    await db_session.refresh(eve)
    assert (eve.auth_provider, eve.external_subject) == ("oidc:entra", "sub-9")


async def test_callback_relabels_an_account_it_created_before(client, db_session, monkeypatch):
    from api.models.user import User

    legacy = User(
        email="eve@corp.com",
        name="Eve",
        password_hash=None,
        role="user",
        auth_provider="oidc",
        external_subject="sub-9",
    )
    db_session.add(legacy)
    await db_session.commit()
    monkeypatch.setattr(settings, "oidc_providers", [_provider()])
    fake = _TokenClient({"email": "eve@corp.com", "sub": "sub-9", "name": "Eve"})
    monkeypatch.setattr(oidc_mod.oauth, "create_client", lambda name: fake)

    resp = await client.get("/auth/oidc/entra/callback", follow_redirects=False)
    assert resp.headers["location"] == "/"
    await db_session.refresh(legacy)
    assert legacy.auth_provider == "oidc:entra"


async def test_callback_refusal_redirects_with_error(client, db_session, monkeypatch):
    from api.models.user import User

    db_session.add(User(email="root@corp.com", name="Root", password_hash="x", role="admin"))
    await db_session.commit()
    monkeypatch.setattr(settings, "oidc_providers", [_provider()])
    fake = _TokenClient({"email": "root@corp.com", "sub": "s", "email_verified": True})
    monkeypatch.setattr(oidc_mod.oauth, "create_client", lambda name: fake)

    resp = await client.get("/auth/oidc/entra/callback", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login?error=sso"
    assert "session" not in resp.cookies


async def test_callback_failure_redirects_with_error(client, monkeypatch):
    monkeypatch.setattr(settings, "oidc_providers", [_provider()])

    class _BoomClient:
        async def authorize_access_token(self, request):
            raise RuntimeError("invalid state / unreachable IdP")

    monkeypatch.setattr(oidc_mod.oauth, "create_client", lambda name: _BoomClient())
    resp = await client.get("/auth/oidc/entra/callback", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login?error=sso"


async def test_callback_missing_identity_claims_rejected(client, monkeypatch):
    monkeypatch.setattr(settings, "oidc_providers", [_provider()])
    monkeypatch.setattr(oidc_mod.oauth, "create_client", lambda name: _TokenClient({"sub": "s"}))
    resp = await client.get("/auth/oidc/entra/callback", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login?error=sso"


async def test_unknown_provider_404(client, monkeypatch):
    monkeypatch.setattr(settings, "oidc_providers", [_provider()])
    monkeypatch.setattr(oidc_mod.oauth, "create_client", lambda name: object())
    resp = await client.get("/auth/oidc/google/login", follow_redirects=False)
    assert resp.status_code == 404


async def test_login_404_when_no_providers(client, monkeypatch):
    monkeypatch.setattr(settings, "oidc_providers", [])
    monkeypatch.setattr(settings, "oidc_enabled", False)
    monkeypatch.setattr(oidc_mod.oauth, "create_client", lambda name: object())
    resp = await client.get("/auth/oidc/entra/login", follow_redirects=False)
    assert resp.status_code == 404
