"""End-to-end OIDC Authorization Code flow against a real Keycloak.

The app runs over ASGI transport while a separate real HTTP client drives the
Keycloak login form; the resulting ``code``/``state`` is handed back to the app,
which performs a genuine back-channel token exchange and ID-token validation
(signature/nonce). Bring up the IdP with ``make idp-dev`` and export
``OIDC_SERVER_METADATA_URL`` (see that target's output).
"""

from __future__ import annotations

import re

import httpx
import pytest

pytestmark = pytest.mark.integration


async def _drive_keycloak_login(authorize_url: str, username: str, password: str) -> str:
    """Submit the Keycloak username/password form; return the callback redirect."""
    async with httpx.AsyncClient(follow_redirects=False, timeout=15.0) as kc:
        page = await kc.get(authorize_url)
        match = re.search(r'action="([^"]+)"', page.text)
        assert match, "Could not find the Keycloak login form action"
        form_action = match.group(1).replace("&amp;", "&")
        submit = await kc.post(
            form_action,
            data={"username": username, "password": password},
            cookies=page.cookies,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    assert submit.status_code in (302, 303), submit.text
    return submit.headers["location"]


async def _callback_path(callback_url: str) -> str:
    # The redirect_uri is http://test/api/auth/oidc/sso/callback, but the ASGI
    # client targets api_app directly (no /api prefix), so strip it for routing.
    # Authlib still uses the stored redirect_uri for the token exchange.
    return callback_url.split("http://test", 1)[-1].replace("/api/auth", "/auth", 1)


async def test_sso_login_provisions_admin_from_group(app_client, oidc_settings) -> None:
    login = await app_client.get("/auth/oidc/sso/login", follow_redirects=False)
    assert login.status_code in (302, 307)

    callback_url = await _drive_keycloak_login(login.headers["location"], "sso-admin", "sso-pass")
    cb = await app_client.get(await _callback_path(callback_url), follow_redirects=False)
    assert cb.status_code == 303
    assert cb.headers["location"] == "/"
    assert "session" in app_client.cookies

    me = await app_client.get("/me")
    assert me.status_code == 200
    body = me.json()
    assert body["email"] == "sso-admin@duckhaven.test"
    assert body["auth_provider"] == "oidc"
    assert body["role"] == "admin"
    assert "users:manage" in body["permissions"]


async def test_sso_user_without_group_is_plain_user(app_client, oidc_settings) -> None:
    login = await app_client.get("/auth/oidc/sso/login", follow_redirects=False)
    callback_url = await _drive_keycloak_login(login.headers["location"], "sso-user", "sso-pass")
    cb = await app_client.get(await _callback_path(callback_url), follow_redirects=False)
    assert cb.status_code == 303

    me = await app_client.get("/me")
    assert me.json()["role"] == "user"
    assert me.json()["permissions"] == []
