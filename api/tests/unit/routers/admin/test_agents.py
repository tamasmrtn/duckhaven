import uuid

import pytest
from httpx import AsyncClient

from api.models.agent import Agent
from api.models.user import Credential, User
from api.services.auth import hash_password


@pytest.fixture
async def admin(db_session):
    u = User(
        email="admin@agents.local", password_hash=hash_password("pw"), name="Admin", role="admin"
    )
    db_session.add(u)
    await db_session.commit()
    await db_session.refresh(u)
    return u


@pytest.fixture
async def admin_client(client: AsyncClient, admin: User):
    await client.post("/auth/login", json={"email": "admin@agents.local", "password": "pw"})
    return client


async def test_bootstrap_creates_token(admin_client: AsyncClient):
    resp = await admin_client.post("/admin/agents/bootstrap")
    assert resp.status_code == 200
    data = resp.json()
    assert data["token"].startswith("dh_boot_")
    assert "expires_at" in data


async def test_list_agents_empty(admin_client: AsyncClient):
    resp = await admin_client.get("/admin/agents")
    assert resp.status_code == 200
    assert resp.json() == []


async def test_revoke_nonexistent_agent(admin_client: AsyncClient):
    resp = await admin_client.delete(f"/admin/agents/{uuid.uuid4()}/credential")
    assert resp.status_code == 404


async def test_revoke_agent_marks_unavailable(admin_client: AsyncClient, db_session, admin: User):
    agent = Agent(name="test-agent", status="healthy")
    db_session.add(agent)
    await db_session.flush()
    cred = Credential(
        user_id=None,
        agent_id=agent.id,
        kind="agent_session",
        token="tok-revoke-test",
        expires_at=None,
    )
    db_session.add(cred)
    await db_session.commit()

    resp = await admin_client.delete(f"/admin/agents/{agent.id}/credential")
    assert resp.status_code == 204

    await db_session.refresh(agent)
    assert agent.status == "unavailable"
