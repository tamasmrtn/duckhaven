"""A request refused for lacking a permission or a data grant leaves an audit event."""

from __future__ import annotations

import pytest
from audit_helpers import audit_events
from conftest import seed_workspace
from sqlalchemy import update

from api.models.agent import Agent
from api.models.catalog import WorkspaceCatalog
from api.models.user import User
from api.services import grants as grant_service
from api.services.agent_registry import registry
from api.services.auth import hash_password


class _Socket:
    async def send_text(self, text: str) -> None:
        pass


@pytest.fixture
async def user(db_session) -> User:
    u = User(email="q@audit.local", password_hash=hash_password("pw"), name="Q", role="user")
    db_session.add(u)
    await db_session.commit()
    await db_session.refresh(u)
    return u


@pytest.fixture
async def authed(client, user):
    await client.post("/auth/login", json={"email": user.email, "password": "pw"})
    return client


@pytest.fixture
async def scoped(db_session, user):
    ws, cat = await seed_workspace(db_session, user_id=user.id)
    await db_session.execute(
        update(WorkspaceCatalog)
        .where(WorkspaceCatalog.workspace_id == ws.id)
        .values(access_mode="scoped")
    )
    agent = Agent(
        name="a",
        status="healthy",
        capabilities={"duckdb_version": "1.5.5", "extensions": ["httpfs"]},
    )
    db_session.add(agent)
    await db_session.commit()
    registry.register(agent.id, _Socket())  # type: ignore[arg-type]
    yield ws, cat, agent
    registry.unregister(agent.id)


async def test_lacking_a_permission_is_recorded_once_a_minute(authed, db_session, user):
    for _ in range(2):
        assert (await authed.get("/admin/users")).status_code == 403

    (event,) = await audit_events(db_session, category="authz")
    assert (event.action, event.outcome, event.actor_email) == (
        "authz.permission_denied",
        "denied",
        user.email,
    )
    assert event.target_name == "GET /admin/users"
    assert event.details == {"permission": "users:manage"}


async def test_a_statement_refused_by_a_grant_is_recorded(authed, db_session, user, scoped):
    ws, _cat, agent = scoped
    sql = "SELECT * FROM analytics.leads"

    resp = await authed.post(
        f"/workspaces/{ws.slug}/queries", json={"sql": sql, "agent_id": str(agent.id)}
    )
    assert resp.status_code == 403

    (event,) = await audit_events(db_session, "authz.grant_denied")
    assert (event.actor_email, event.workspace_slug, event.error_code) == (
        user.email,
        ws.slug,
        "grant_denied",
    )
    assert event.target_name == "POST /workspaces/{workspace}/queries"
    assert event.details["statement"] == sql
    assert event.details["reason"] == resp.json()["message"]


async def test_an_allowed_statement_records_nothing(authed, db_session, scoped):
    ws, _cat, agent = scoped
    resp = await authed.post(
        f"/workspaces/{ws.slug}/queries", json={"sql": "SELECT 1", "agent_id": str(agent.id)}
    )

    assert resp.status_code == 202, resp.text
    assert await audit_events(db_session, category="authz") == []


async def test_a_refusal_outside_a_request_is_recorded_as_its_principals(db_session, user, scoped):
    """A schedule firing has no request; the account it runs as is the actor."""
    ws, cat, _agent = scoped

    with pytest.raises(grant_service.GrantDenied):
        await grant_service.assert_query_access(
            db_session, ws.id, user.id, "SELECT * FROM analytics.leads", cat.slug, [cat]
        )

    (event,) = await audit_events(db_session, "authz.grant_denied")
    assert (event.actor_email, event.target_name, event.request_id) == (user.email, None, None)
