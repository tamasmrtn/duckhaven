"""Creating and deleting workspaces, and changing who is in one, leave an audit event."""

from __future__ import annotations

import pytest
from audit_helpers import actions, audit_events
from httpx import AsyncClient

from api.models.agent import Agent
from api.models.user import User
from api.services.auth import hash_password


async def _user(db_session, email: str, role: str = "user") -> User:
    u = User(email=email, password_hash=hash_password("pw"), name=email, role=role)
    db_session.add(u)
    await db_session.commit()
    await db_session.refresh(u)
    return u


async def _login(client: AsyncClient, email: str) -> None:
    resp = await client.post("/auth/login", json={"email": email, "password": "pw"})
    assert resp.status_code == 200, resp.text


@pytest.fixture
async def owner(db_session) -> User:
    """An admin: creating the workspace these events are about takes `workspaces:create`."""
    return await _user(db_session, "owner@audit.local", role="admin")


async def test_creating_a_workspace_records_it_and_its_owner(client, db_session, owner):
    await _login(client, owner.email)
    assert (
        await client.post("/workspaces", json={"slug": "ops", "name": "Ops"})
    ).status_code == 201

    create, member = await audit_events(db_session)
    assert (create.action, create.workspace_slug, create.target_name) == (
        "workspace.create",
        "ops",
        "ops",
    )
    assert (member.action, member.subject_email, member.after, member.self_change) == (
        "workspace_member.add",
        owner.email,
        {"role": "owner"},
        True,
    )


async def test_adding_a_member_is_recorded(client, db_session, owner):
    analyst = await _user(db_session, "analyst@audit.local")
    await _login(client, owner.email)
    await client.post("/workspaces", json={"slug": "ops", "name": "Ops"})

    resp = await client.post(
        "/workspaces/ops/members", json={"user_id": str(analyst.id), "role": "writer"}
    )
    assert resp.status_code == 201, resp.text

    (event,) = (await audit_events(db_session, "workspace_member.add"))[1:]
    assert (event.subject_email, event.after, event.self_change, event.actor_email) == (
        analyst.email,
        {"role": "writer"},
        False,
        owner.email,
    )


async def test_an_admin_adding_themselves_is_marked_as_their_own_change(client, db_session, owner):
    """The case the audit log is most needed for: an admin who can make
    themselves a member of any workspace, and so reach its data."""
    admin = await _user(db_session, "admin@audit.local", role="admin")
    await _login(client, owner.email)
    await client.post("/workspaces", json={"slug": "ops", "name": "Ops"})
    await _login(client, admin.email)
    path = f"/admin/users/{admin.id}/workspaces/ops"

    assert (await client.put(path, json={"role": "reader"})).status_code == 200
    assert (await client.put(path, json={"role": "reader"})).status_code == 200  # unchanged
    assert (await client.put(path, json={"role": "owner"})).status_code == 200
    assert (await client.delete(path)).status_code == 204
    assert (await client.delete(path)).status_code == 204  # not a member: nothing to record

    added, changed, removed = (await audit_events(db_session))[2:]
    assert [added.action, changed.action, removed.action] == [
        "workspace_member.add",
        "workspace_member.role_change",
        "workspace_member.remove",
    ]
    assert all(e.self_change and e.subject_email == admin.email for e in (added, changed, removed))
    assert (changed.before, changed.after) == ({"role": "reader"}, {"role": "owner"})
    assert removed.before == {"role": "owner"}


async def test_choosing_a_default_compute_is_recorded(client, db_session, owner):
    agent = Agent(name="shared", status="healthy", access_mode="open")
    db_session.add(agent)
    await db_session.commit()
    await _login(client, owner.email)
    await client.post("/workspaces", json={"slug": "ops", "name": "Ops"})

    set_ = await client.patch("/workspaces/ops", json={"default_agent_id": str(agent.id)})
    assert set_.status_code == 200, set_.text
    await client.patch("/workspaces/ops", json={"default_agent_id": str(agent.id)})  # unchanged
    await client.patch("/workspaces/ops", json={"name": "Operations"})  # not access
    await client.patch("/workspaces/ops", json={"default_agent_id": None})

    first, cleared = await audit_events(db_session, "workspace.default_agent_change")
    assert (first.before, first.after) == (
        {"default_agent_id": None},
        {"default_agent_id": str(agent.id)},
    )
    assert cleared.after == {"default_agent_id": None}


async def test_deleting_a_workspace_records_what_went_with_it(client, db_session, owner):
    analyst = await _user(db_session, "analyst@audit.local")
    await _login(client, owner.email)
    await client.post("/workspaces", json={"slug": "ops", "name": "Ops"})
    await client.post("/workspaces/ops/members", json={"user_id": str(analyst.id)})

    assert (await client.delete("/workspaces/ops")).status_code == 204

    assert (await actions(db_session))[-1] == "workspace.delete"
    (event,) = await audit_events(db_session, "workspace.delete")
    assert event.workspace_slug == "ops"
    assert event.details == {"members_removed": 2, "catalogs_detached": 0}
