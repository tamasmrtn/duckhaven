"""First-admin setup + auth lifecycle against a real Postgres schema.

Exercises the real setup-token gating (`POST /setup/admin`), the login/logout
session-cookie cycle, and `/me`, all over the live ``api_app``. Polaris is not
touched here, but `app_client` still wires the real client (the integration
env has both up).
"""

from __future__ import annotations

import pytest

from api.config import settings

pytestmark = pytest.mark.integration

TOKEN = "setup-token-integration"


@pytest.fixture
def setup_token(tmp_path, monkeypatch) -> str:
    """Write a one-shot setup token file and point settings at it."""
    path = tmp_path / "setup_token"
    path.write_text(TOKEN)
    monkeypatch.setattr(settings, "setup_token_path", path)
    return TOKEN


async def test_setup_status_reports_needs_admin_when_empty(app_client) -> None:
    resp = await app_client.get("/setup/status")
    assert resp.status_code == 200
    assert resp.json() == {"needs_admin": True}


async def test_create_first_admin_rejects_missing_and_wrong_token(app_client, setup_token) -> None:
    body = {"email": "admin@x.test", "password": "password123", "name": "Admin"}

    missing = await app_client.post("/setup/admin", json=body)
    assert missing.status_code == 403

    wrong = await app_client.post("/setup/admin", json=body, headers={"X-Setup-Token": "nope"})
    assert wrong.status_code == 403


async def test_setup_then_login_logout_me(app_client, setup_token) -> None:
    body = {"email": "admin@x.test", "password": "password123", "name": "Admin"}

    created = await app_client.post(
        "/setup/admin", json=body, headers={"X-Setup-Token": setup_token}
    )
    assert created.status_code == 201, created.text
    assert created.json()["role"] == "admin"
    assert "session" in app_client.cookies

    # /me resolves the authenticated admin from the session cookie.
    me = await app_client.get("/me")
    assert me.status_code == 200
    assert me.json()["email"] == "admin@x.test"

    # The token is single-use: a second setup attempt is now refused.
    replay = await app_client.post(
        "/setup/admin", json=body, headers={"X-Setup-Token": setup_token}
    )
    assert replay.status_code == 409

    # Logout clears the session; /me is then unauthorized.
    logout = await app_client.post("/auth/logout")
    assert logout.status_code == 204
    app_client.cookies.clear()
    assert (await app_client.get("/me")).status_code == 401


async def test_login_rejects_bad_credentials(app_client, admin_user) -> None:
    resp = await app_client.post(
        "/auth/login", json={"email": admin_user.email, "password": "wrong"}
    )
    assert resp.status_code == 401


async def test_break_glass_local_admin_when_ldap_unreachable(
    app_client, admin_user, monkeypatch
) -> None:
    """Local-first auth lets the break-glass admin in even with LDAP enabled but
    the directory down — the LDAP bind is never reached for a local account."""
    monkeypatch.setattr(settings, "ldap_enabled", True)
    monkeypatch.setattr(settings, "ldap_server_uri", "ldap://127.0.0.1:1")
    resp = await app_client.post(
        "/auth/login", json={"email": admin_user.email, "password": "integration-pw-123"}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["auth_provider"] == "local"


async def test_oidc_callback_redirects_to_error_when_idp_down(app_client, monkeypatch) -> None:
    """A failed callback (unreachable IdP / bad state) degrades to a friendly
    error redirect, never a 500 or a leaked token."""
    monkeypatch.setattr(settings, "oidc_enabled", True)
    monkeypatch.setattr(
        settings,
        "oidc_server_metadata_url",
        "http://127.0.0.1:1/.well-known/openid-configuration",
    )
    monkeypatch.setattr(settings, "oidc_client_id", "duckhaven-api")
    from api.services.oidc import register_oidc, reset_oidc_clients

    reset_oidc_clients()
    register_oidc()

    # The single-provider fields synthesize a provider with id "sso".
    resp = await app_client.get("/auth/oidc/sso/callback", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login?error=sso"
