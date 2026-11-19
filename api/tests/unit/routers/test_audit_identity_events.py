"""Accounts, their global roles and their tokens leave an audit event when they change."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from audit_helpers import actions, audit_events
from httpx import AsyncClient

from api.config import settings
from api.models.user import User
from api.services.auth import hash_password
from api.services.provisioning import provision_federated_user


async def _user(db_session, email: str, role: str = "user") -> User:
    u = User(email=email, password_hash=hash_password("pw"), name=email, role=role)
    db_session.add(u)
    await db_session.commit()
    await db_session.refresh(u)
    return u


@pytest.fixture
async def admin_client(client: AsyncClient, db_session) -> AsyncClient:
    admin = await _user(db_session, "admin@audit.local", role="admin")
    await client.post("/auth/login", json={"email": admin.email, "password": "pw"})
    client.admin = admin  # type: ignore[attr-defined]
    return client


async def test_creating_and_changing_a_user_is_recorded(admin_client, db_session):
    created = await admin_client.post(
        "/admin/users",
        json={"email": "new@audit.local", "name": "New", "password": "pw-pw-pw-pw", "role": "user"},
    )
    assert created.status_code == 201, created.text
    path = f"/admin/users/{created.json()['id']}"
    await admin_client.patch(path, json={"role": "admin"})
    await admin_client.patch(path, json={"role": "admin"})  # unchanged
    await admin_client.patch(path, json={"is_active": False})
    await admin_client.patch(path, json={"is_active": True})
    await admin_client.post(f"{path}/revoke-sessions")

    assert await actions(db_session) == [
        "user.create",
        "user.role_change",
        "user.deactivate",
        "user.reactivate",
        "user.sessions_revoke",
    ]
    events = await audit_events(db_session)
    assert {e.subject_email for e in events} == {"new@audit.local"}
    assert (events[1].before, events[1].after) == ({"role": "user"}, {"role": "admin"})
    assert events[4].details == {"sessions_revoked": 0}


async def test_a_refused_change_records_nothing(admin_client, db_session):
    """The last active admin cannot demote themselves."""
    resp = await admin_client.patch(f"/admin/users/{admin_client.admin.id}", json={"role": "user"})

    assert resp.status_code == 409
    assert await audit_events(db_session) == []


async def test_an_admin_changing_their_own_role_is_marked(admin_client, db_session):
    await _user(db_session, "second-admin@audit.local", role="admin")
    await admin_client.patch(f"/admin/users/{admin_client.admin.id}", json={"role": "user"})

    (event,) = await audit_events(db_session)
    assert (event.action, event.self_change) == ("user.role_change", True)


async def test_a_service_accounts_life_is_recorded(admin_client, db_session):
    created = await admin_client.post("/admin/service-accounts", json={"name": "ci"})
    assert created.status_code == 201, created.text
    path = f"/admin/service-accounts/{created.json()['id']}"
    await admin_client.patch(path, json={"role": "admin"})
    await admin_client.patch(path, json={"is_active": False})
    assert (await admin_client.delete(path)).status_code == 204

    assert await actions(db_session) == [
        "service_account.create",
        "service_account.role_change",
        "service_account.deactivate",
        "service_account.delete",
    ]
    deleted = (await audit_events(db_session))[-1]
    assert deleted.before == {"role": "admin", "is_active": False}
    assert deleted.details == {"memberships_removed": 0, "grants_removed": 0}


async def test_tokens_are_recorded_without_their_secret(admin_client, db_session):
    account = (await admin_client.post("/admin/service-accounts", json={"name": "ci"})).json()
    path = f"/admin/service-accounts/{account['id']}/pats"
    issued = await admin_client.post(path, json={"expires_in_days": 30})
    assert issued.status_code == 201, issued.text
    assert (await admin_client.delete(f"{path}/{issued.json()['id']}")).status_code == 204
    own = await admin_client.post("/me/pats", json={})
    assert own.status_code == 201, own.text

    events = [e for e in await audit_events(db_session) if e.category == "credential"]
    assert [e.action for e in events] == ["pat.issue", "pat.revoke", "pat.issue"]
    sa_issue, sa_revoke, mine = events
    assert sa_issue.target_id == issued.json()["id"]
    assert sa_issue.subject_email == account["email"] and not sa_issue.self_change
    assert sa_issue.after["expires_at"] is not None
    assert sa_revoke.before["created_at"] is not None
    assert (mine.subject_email, mine.self_change) == (admin_client.admin.email, True)
    for event, secret in ((sa_issue, issued.json()["token"]), (mine, own.json()["token"])):
        stored = json.dumps([event.before, event.after, event.details, event.target_name])
        assert secret not in stored


async def test_the_first_admin_is_recorded_as_acting_for_themselves(
    client, db_session, tmp_path: Path, monkeypatch
):
    token_file = tmp_path / "setup_token"
    token_file.write_text("setup-secret")
    monkeypatch.setattr(settings, "setup_token_path", token_file)

    resp = await client.post(
        "/setup/admin",
        json={"email": "first@audit.local", "password": "hunter2hunter2", "name": "First"},
        headers={"X-Setup-Token": "setup-secret"},
    )
    assert resp.status_code == 201, resp.text

    (event,) = await audit_events(db_session)
    assert (event.action, event.actor_email, event.subject_email, event.after) == (
        "setup.first_admin",
        "first@audit.local",
        "first@audit.local",
        {"role": "admin"},
    )
    assert event.details == {"authorized_by": "setup_token"}
    (sign_in,) = await audit_events(db_session, "auth.sign_in")
    assert sign_in.details == {"method": "setup_token", "provider": "local"}


async def test_an_identity_provider_creating_and_reroling_an_account_is_recorded(db_session):
    async def sign_in(role: str) -> User:
        return await provision_federated_user(
            db_session,
            email="fed@audit.local",
            name="Fed",
            subject="sub-1",
            provider="oidc:corp",
            role=role,
            email_verified=True,
        )

    await sign_in("user")
    await sign_in("user")  # the same role again: nothing changed
    await sign_in("admin")

    provision, change = await audit_events(db_session)
    assert (provision.action, provision.actor_type, provision.after) == (
        "user.provision",
        "idp",
        {"role": "user"},
    )
    assert (change.action, change.actor_type, change.self_change) == (
        "user.role_change",
        "idp",
        False,
    )
    assert (change.before, change.after) == ({"role": "user"}, {"role": "admin"})
    assert change.details == {"provider": "oidc:corp"}


async def test_a_held_demotion_of_the_last_admin_is_recorded_as_denied(db_session):
    async def sign_in(role: str) -> User:
        return await provision_federated_user(
            db_session,
            email="last@audit.local",
            name="Last",
            subject="sub-1",
            provider="oidc:corp",
            role=role,
            email_verified=True,
        )

    await sign_in("admin")
    await sign_in("user")

    [held] = await audit_events(db_session, "user.role_change")
    assert (held.outcome, held.actor_type, held.error_code) == ("denied", "idp", None)
    assert (held.before, held.after) == ({"role": "admin"}, {"role": "user"})
    assert held.details == {"provider": "oidc:corp", "reason": "last_active_admin"}
