import api.routers.oidc as oidc_mod
from api.config import settings
from api.services.auth import get_user_by_email


class _TokenClient:
    """Stand-in for the Authlib client: returns pre-baked ID-token claims."""

    def __init__(self, claims: dict):
        self._claims = claims

    async def authorize_access_token(self, request):
        return {"userinfo": self._claims}


async def test_callback_provisions_user_and_starts_session(client, db_session, monkeypatch):
    monkeypatch.setattr(settings, "oidc_enabled", True)
    monkeypatch.setattr(settings, "oidc_group_role_map", {"admins": "admin"})
    fake = _TokenClient(
        {"email": "eve@corp.com", "sub": "sub-9", "name": "Eve", "groups": ["admins"]}
    )
    monkeypatch.setattr(oidc_mod.oauth, "create_client", lambda name: fake)

    resp = await client.get("/auth/oidc/callback", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/"
    assert "session" in resp.cookies

    eve = await get_user_by_email(db_session, "eve@corp.com")
    assert eve is not None
    assert eve.auth_provider == "oidc"
    assert eve.role == "admin"
    assert eve.password_hash is None


async def test_callback_failure_redirects_with_error(client, monkeypatch):
    monkeypatch.setattr(settings, "oidc_enabled", True)

    class _BoomClient:
        async def authorize_access_token(self, request):
            raise RuntimeError("invalid state / unreachable IdP")

    monkeypatch.setattr(oidc_mod.oauth, "create_client", lambda name: _BoomClient())
    resp = await client.get("/auth/oidc/callback", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login?error=sso"


async def test_callback_missing_email_rejected(client, monkeypatch):
    monkeypatch.setattr(settings, "oidc_enabled", True)
    monkeypatch.setattr(oidc_mod.oauth, "create_client", lambda name: _TokenClient({"sub": "s"}))
    resp = await client.get("/auth/oidc/callback", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login?error=sso"


async def test_login_404_when_disabled(client, monkeypatch):
    monkeypatch.setattr(settings, "oidc_enabled", False)
    monkeypatch.setattr(oidc_mod.oauth, "create_client", lambda name: object())
    resp = await client.get("/auth/oidc/login", follow_redirects=False)
    assert resp.status_code == 404
