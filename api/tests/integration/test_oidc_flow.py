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
from sqlalchemy import select, update

from api.models.user import User

pytestmark = pytest.mark.integration


async def _drive_keycloak_login(authorize_url: str, username: str, password: str) -> str:
    """Submit the Keycloak username/password form; return the callback redirect."""
    async with httpx.AsyncClient(follow_redirects=False, timeout=15.0) as kc:
        page = await kc.get(authorize_url)
        match = re.search(r'action="([^"]+)"', page.text)
        assert match, "Could not find the Keycloak login form action"
        form_action = match.group(1).replace("&amp;", "&")
        # Keycloak marks its login-session cookies Secure, which a cookie jar
        # withholds over the plain-HTTP dev realm, so send them as a header.
        submit = await kc.post(
            form_action,
            data={"username": username, "password": password},
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Cookie": "; ".join(f"{k}={v}" for k, v in page.cookies.items()),
            },
        )
    assert submit.status_code in (302, 303), submit.text
    return submit.headers["location"]


async def _callback_path(callback_url: str) -> str:
    # The redirect_uri is http://test/api/auth/oidc/sso/callback, but the ASGI
    # client targets api_app directly (no /api prefix), so strip it for routing.
    # Authlib still uses the stored redirect_uri for the token exchange.
    return callback_url.split("http://test", 1)[-1].replace("/api/auth", "/auth", 1)


async def _sign_in(app_client, username: str) -> httpx.Response:
    """Run the whole flow for one Keycloak user; return the callback's response."""
    login = await app_client.get("/auth/oidc/sso/login", follow_redirects=False)
    assert login.status_code in (302, 307)
    callback_url = await _drive_keycloak_login(login.headers["location"], username, "sso-pass")
    # The session middleware holding the state is built before the conftest turns
    # Secure cookies off, so its cookie is Secure: hand it to the callback directly.
    return await app_client.get(
        await _callback_path(callback_url),
        headers={"Cookie": f"dh_oidc={login.cookies['dh_oidc']}"},
        follow_redirects=False,
    )


async def _account(db_session, email: str) -> User:
    db_session.expire_all()
    return (await db_session.scalars(select(User).where(User.email == email))).one()


async def test_sso_login_provisions_admin_from_group(app_client, oidc_settings) -> None:
    cb = await _sign_in(app_client, "sso-admin")
    assert cb.status_code == 303
    assert cb.headers["location"] == "/"
    assert "session" in app_client.cookies

    me = await app_client.get("/me")
    assert me.status_code == 200
    body = me.json()
    assert body["email"] == "sso-admin@duckhaven.test"
    assert body["auth_provider"] == "oidc:sso"
    assert body["role"] == "admin"
    assert "users:manage" in body["permissions"]


async def test_sso_user_without_group_is_plain_user(app_client, oidc_settings) -> None:
    cb = await _sign_in(app_client, "sso-user")
    assert cb.status_code == 303

    me = await app_client.get("/me")
    assert me.json()["role"] == "user"
    assert me.json()["permissions"] == []


async def test_an_account_made_under_the_plain_oidc_name_is_relabelled(
    app_client, db_session, oidc_settings
) -> None:
    """An account a provider created as plain `oidc` signs in as itself."""
    assert (await _sign_in(app_client, "sso-user")).headers["location"] == "/"
    account = await _account(db_session, "sso-user@duckhaven.test")
    await db_session.execute(update(User).where(User.id == account.id).values(auth_provider="oidc"))
    await db_session.commit()
    app_client.cookies.clear()

    cb = await _sign_in(app_client, "sso-user")
    assert cb.headers["location"] == "/"
    me = (await app_client.get("/me")).json()
    assert (me["id"], me["auth_provider"]) == (str(account.id), "oidc:sso")


async def test_an_account_another_provider_holds_is_not_signed_in_to(
    app_client, db_session, oidc_settings
) -> None:
    """The same email and subject from this provider do not take the account over."""
    await _sign_in(app_client, "sso-user")
    account = await _account(db_session, "sso-user@duckhaven.test")
    await db_session.execute(
        update(User).where(User.id == account.id).values(auth_provider="oidc:other")
    )
    await db_session.commit()
    app_client.cookies.clear()

    cb = await _sign_in(app_client, "sso-user")
    assert cb.headers["location"] == "/login?error=sso"
    assert "session" not in app_client.cookies
    after = await _account(db_session, "sso-user@duckhaven.test")
    assert (after.auth_provider, after.external_subject) == ("oidc:other", account.external_subject)
