"""Signing in, failing to, being rate limited and signing out leave an audit event."""

from __future__ import annotations

from audit_helpers import audit_events
from httpx import AsyncClient

import api.routers.oidc as oidc_mod
from api.config import OidcProvider, settings
from api.models.user import User
from api.services.auth import hash_password
from api.services.login_limiter import PER_ACCOUNT


async def _user(db_session, email: str = "ann@audit.local") -> User:
    u = User(email=email, password_hash=hash_password("right-pw"), name="Ann", role="user")
    db_session.add(u)
    await db_session.commit()
    await db_session.refresh(u)
    return u


async def _login(client: AsyncClient, email: str, password: str) -> int:
    resp = await client.post("/auth/login", json={"email": email, "password": password})
    return resp.status_code


async def test_a_sign_in_and_sign_out_are_recorded_as_the_user(client, db_session):
    ann = await _user(db_session)

    assert await _login(client, ann.email, "right-pw") == 200
    assert (await client.post("/auth/logout")).status_code == 204

    sign_in, sign_out = await audit_events(db_session, category="auth")
    assert (sign_in.action, sign_in.actor_email, sign_in.subject_email) == (
        "auth.sign_in",
        ann.email,
        None,
    )
    assert sign_in.details == {"method": "password", "provider": "local"}
    assert sign_in.source_ip is not None and sign_in.request_id is not None
    assert (sign_out.action, sign_out.actor_email) == ("auth.sign_out", ann.email)


async def test_a_failed_sign_in_records_what_was_tried_and_never_the_password(client, db_session):
    ann = await _user(db_session)

    assert await _login(client, ann.email, "wrong-pw") == 401
    assert await _login(client, "nobody@audit.local", "wrong-pw") == 401

    known, unknown = await audit_events(db_session, "auth.sign_in_failed")
    # The same shape whether or not the account exists, so the log a reader sees
    # does not answer that question either.
    for event, tried in ((known, ann.email), (unknown, "nobody@audit.local")):
        assert (event.target_name, event.target_id, event.outcome) == (tried, None, "failure")
        assert (event.actor_type, event.actor_id) == ("anonymous", None)
        assert event.details == {"method": "password"}
        assert "wrong-pw" not in str([event.before, event.after, event.details])


async def test_repeated_failures_are_recorded_once_a_minute(client, db_session):
    for _ in range(3):
        assert await _login(client, "nobody@audit.local", "wrong-pw") == 401

    assert len(await audit_events(db_session, "auth.sign_in_failed")) == 1


async def test_being_rate_limited_is_recorded(client, db_session):
    for _ in range(PER_ACCOUNT):
        await _login(client, "target@audit.local", "guess")

    assert await _login(client, "target@audit.local", "guess") == 429

    (event,) = await audit_events(db_session, "auth.rate_limited")
    assert (event.target_name, event.outcome, event.error_code) == (
        "target@audit.local",
        "denied",
        "too_many_sign_in_attempts",
    )


def _provider() -> OidcProvider:
    return OidcProvider(
        id="entra",
        label="Microsoft",
        server_metadata_url="https://idp.test/.well-known/openid-configuration",
        client_id="cid",
        client_secret="sec",
    )


class _TokenClient:
    def __init__(self, claims: dict | None):
        self._claims = claims

    async def authorize_access_token(self, request):
        if self._claims is None:
            raise RuntimeError("state mismatch")
        return {"userinfo": self._claims}


async def test_a_single_sign_on_is_recorded_with_its_provider(client, db_session, monkeypatch):
    monkeypatch.setattr(settings, "oidc_providers", [_provider()])
    claims = {"email": "eve@corp.com", "sub": "sub-9", "name": "Eve", "email_verified": True}
    monkeypatch.setattr(oidc_mod.oauth, "create_client", lambda name: _TokenClient(claims))

    resp = await client.get("/auth/oidc/entra/callback", follow_redirects=False)
    assert resp.headers["location"] == "/"

    (event,) = await audit_events(db_session, "auth.sign_in")
    assert (event.actor_email, event.details) == (
        "eve@corp.com",
        {"method": "oidc", "provider": "oidc:entra"},
    )
    assert [e.action for e in await audit_events(db_session)] == ["user.provision"]


async def test_a_failed_single_sign_on_is_recorded_with_why(client, db_session, monkeypatch):
    await _user(db_session, "eve@corp.com")  # a local account: SSO may not take it over
    monkeypatch.setattr(settings, "oidc_providers", [_provider()])
    tries = iter(
        [
            None,
            {"sub": "sub-9"},
            {"email": "eve@corp.com", "sub": "sub-9", "email_verified": True},
        ]
    )
    monkeypatch.setattr(oidc_mod.oauth, "create_client", lambda name: _TokenClient(next(tries)))

    for _ in range(3):
        resp = await client.get("/auth/oidc/entra/callback", follow_redirects=False)
        assert resp.headers["location"] == "/login?error=sso"

    events = await audit_events(db_session, "auth.sign_in_failed")
    assert [(e.target_name, e.details["reason"]) for e in events] == [
        (None, "callback_failed"),
        (None, "missing_identity_claims"),
        ("eve@corp.com", "refused"),
    ]
    assert {e.details["provider"] for e in events} == {"oidc:entra"}
