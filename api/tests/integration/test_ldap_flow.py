"""LDAP bind + group-mapping against a real OpenLDAP.

Uses the live ``api_app`` (ASGI) — the LDAP work happens server-side inside the
login request, so no browser redirect is involved. Bring up the directory with
``make idp-dev`` and export ``LDAP_SERVER_URI`` (see that target's output).
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration

LDAP_EMAIL = "ldap-admin@duckhaven.test"
LDAP_PASSWORD = "ldap-pass"


async def test_ldap_login_provisions_user_with_mapped_role(app_client, ldap_settings) -> None:
    resp = await app_client.post(
        "/auth/login", json={"email": LDAP_EMAIL, "password": LDAP_PASSWORD}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["auth_provider"] == "ldap"
    assert body["role"] == "admin"  # from memberOf cn=dh-admins
    assert "users:manage" in body["permissions"]

    me = await app_client.get("/me")
    assert me.status_code == 200
    assert me.json()["email"] == LDAP_EMAIL


async def test_ldap_wrong_password_denied(app_client, ldap_settings) -> None:
    resp = await app_client.post("/auth/login", json={"email": LDAP_EMAIL, "password": "wrong"})
    assert resp.status_code == 401


async def test_ldap_unknown_user_denied(app_client, ldap_settings) -> None:
    resp = await app_client.post(
        "/auth/login", json={"email": "ghost@duckhaven.test", "password": "x"}
    )
    assert resp.status_code == 401
