"""Agent access-mode and grant management (/admin/agents/{id}/access*)."""

import uuid

import pytest
import pytest_asyncio
from conftest import seed_workspace
from httpx import ASGITransport, AsyncClient

from api.main import api_app
from api.models.agent import Agent
from api.models.agent_grant import AgentGrant
from api.models.user import User
from api.services.auth import hash_password


@pytest_asyncio.fixture
async def admin(db_session):
    u = User(email="admin@aa.local", password_hash=hash_password("pw"), name="Admin", role="admin")
    db_session.add(u)
    await db_session.commit()
    await db_session.refresh(u)
    return u


@pytest_asyncio.fixture
async def plain(db_session):
    u = User(email="plain@aa.local", password_hash=hash_password("pw"), name="Plain", role="user")
    db_session.add(u)
    await db_session.commit()
    await db_session.refresh(u)
    return u


@pytest_asyncio.fixture
async def agent(db_session):
    a = Agent(name="fleet-1", status="healthy")
    db_session.add(a)
    await db_session.commit()
    await db_session.refresh(a)
    return a


@pytest_asyncio.fixture
async def admin_client(client: AsyncClient, admin: User):
    await client.post("/auth/login", json={"email": "admin@aa.local", "password": "pw"})
    return client


@pytest_asyncio.fixture
async def plain_client(client: AsyncClient, plain: User):
    """A second cookie jar.

    Depends on `client` so the dependency overrides are installed, but must not
    reuse it: logging in twice on one AsyncClient replaces the session cookie, and
    these tests need the admin and the grantee acting concurrently.
    """
    async with AsyncClient(transport=ASGITransport(app=api_app), base_url="http://test") as c:
        await c.post("/auth/login", json={"email": "plain@aa.local", "password": "pw"})
        yield c


# --- reading -----------------------------------------------------------------


async def test_access_payload_ships_grants_and_candidate_principals(
    admin_client: AsyncClient, agent: Agent, plain: User, db_session
):
    """One response drives the whole Access tab, so the picker needs no second call."""
    ws, _ = await seed_workspace(db_session, user_id=plain.id, slug="analytics", name="Analytics")
    resp = await admin_client.get(f"/admin/agents/{agent.id}/access")
    assert resp.status_code == 200
    body = resp.json()
    assert body["access_mode"] == "open"
    assert body["grants"] == []
    kinds = {(p["kind"], p["name"]) for p in body["principals"]}
    assert ("user", "Plain") in kinds
    assert ("workspace", "Analytics") in kinds


async def test_access_requires_the_admin_tier(plain_client: AsyncClient, agent: Agent):
    """`use` (which an open agent grants everyone) is not enough to see the ACL."""
    assert (await plain_client.get(f"/admin/agents/{agent.id}/access")).status_code == 403


async def test_operate_tier_still_cannot_read_the_acl(
    plain_client: AsyncClient, agent: Agent, plain: User, db_session
):
    db_session.add(AgentGrant(agent_id=agent.id, user_id=plain.id, tier="operate"))
    await db_session.commit()
    assert (await plain_client.get(f"/admin/agents/{agent.id}/access")).status_code == 403


# --- access mode -------------------------------------------------------------


async def test_switching_to_restricted_hides_the_agent_from_others(
    admin_client: AsyncClient, plain_client: AsyncClient, agent: Agent
):
    assert len((await plain_client.get("/agents")).json()) == 1

    resp = await admin_client.patch(
        f"/admin/agents/{agent.id}/access-mode", json={"access_mode": "restricted"}
    )
    assert resp.status_code == 200
    assert resp.json()["access_mode"] == "restricted"

    assert (await plain_client.get("/agents")).json() == []


async def test_unknown_access_mode_rejected(admin_client: AsyncClient, agent: Agent):
    resp = await admin_client.patch(
        f"/admin/agents/{agent.id}/access-mode", json={"access_mode": "public"}
    )
    assert resp.status_code == 422


# --- granting ----------------------------------------------------------------


async def test_grant_to_a_user_creates_then_updates(
    admin_client: AsyncClient, agent: Agent, plain: User
):
    created = await admin_client.put(
        f"/admin/agents/{agent.id}/grants", json={"user_id": str(plain.id), "tier": "use"}
    )
    assert created.status_code == 201
    assert created.json()["tier"] == "use"
    assert created.json()["user_name"] == "Plain"

    updated = await admin_client.put(
        f"/admin/agents/{agent.id}/grants", json={"user_id": str(plain.id), "tier": "operate"}
    )
    assert updated.status_code == 200
    assert updated.json()["tier"] == "operate"
    # An upsert, not a second row.
    assert len((await admin_client.get(f"/admin/agents/{agent.id}/access")).json()["grants"]) == 1


async def test_grant_to_a_workspace(
    admin_client: AsyncClient, agent: Agent, plain: User, db_session
):
    ws, _ = await seed_workspace(db_session, user_id=plain.id, slug="team", name="Team")
    resp = await admin_client.put(
        f"/admin/agents/{agent.id}/grants", json={"workspace_id": str(ws.id), "tier": "operate"}
    )
    assert resp.status_code == 201
    assert resp.json()["workspace_name"] == "Team"
    assert resp.json()["user_id"] is None


async def test_a_workspace_cannot_be_granted_admin(
    admin_client: AsyncClient, agent: Agent, plain: User, db_session
):
    """Delegating grant/revoke to "whoever is in workspace W" would make the ACL
    unauditable, so Tier 3 is user-only."""
    ws, _ = await seed_workspace(db_session, user_id=plain.id, slug="team2", name="Team2")
    resp = await admin_client.put(
        f"/admin/agents/{agent.id}/grants", json={"workspace_id": str(ws.id), "tier": "admin"}
    )
    assert resp.status_code == 422


@pytest.mark.parametrize(
    "body",
    [
        {"tier": "use"},  # neither principal
        {"user_id": str(uuid.uuid4()), "workspace_id": str(uuid.uuid4()), "tier": "use"},  # both
    ],
    ids=["no-principal", "both-principals"],
)
async def test_grant_needs_exactly_one_principal(admin_client: AsyncClient, agent: Agent, body):
    resp = await admin_client.put(f"/admin/agents/{agent.id}/grants", json=body)
    assert resp.status_code == 422


async def test_grant_to_an_unknown_principal_is_422_not_500(
    admin_client: AsyncClient, agent: Agent
):
    resp = await admin_client.put(
        f"/admin/agents/{agent.id}/grants", json={"user_id": str(uuid.uuid4()), "tier": "use"}
    )
    assert resp.status_code == 422


async def test_unknown_tier_rejected(admin_client: AsyncClient, agent: Agent, plain: User):
    resp = await admin_client.put(
        f"/admin/agents/{agent.id}/grants", json={"user_id": str(plain.id), "tier": "root"}
    )
    assert resp.status_code == 422


# --- revoking ----------------------------------------------------------------


async def test_revoking_removes_access(
    admin_client: AsyncClient, plain_client: AsyncClient, agent: Agent, plain: User, db_session
):
    agent.access_mode = "restricted"
    db_session.add(agent)
    await db_session.commit()

    created = await admin_client.put(
        f"/admin/agents/{agent.id}/grants", json={"user_id": str(plain.id), "tier": "use"}
    )
    grant_id = created.json()["id"]
    assert len((await plain_client.get("/agents")).json()) == 1

    resp = await admin_client.delete(f"/admin/agents/{agent.id}/grants/{grant_id}")
    assert resp.status_code == 204
    assert (await plain_client.get("/agents")).json() == []


async def test_deleting_a_grant_from_another_agent_404s(
    admin_client: AsyncClient, agent: Agent, plain: User, db_session
):
    """The grant id is scoped to the agent in the path, so a cross-agent id cannot
    be used to revoke something the caller does not administer."""
    other = Agent(name="other", status="healthy")
    db_session.add(other)
    await db_session.commit()
    await db_session.refresh(other)
    created = await admin_client.put(
        f"/admin/agents/{other.id}/grants", json={"user_id": str(plain.id), "tier": "use"}
    )
    grant_id = created.json()["id"]

    resp = await admin_client.delete(f"/admin/agents/{agent.id}/grants/{grant_id}")
    assert resp.status_code == 404


async def test_a_deleted_agent_grants_nothing(
    admin_client: AsyncClient, plain_client: AsyncClient, agent: Agent, plain: User
):
    """The grant row is ON DELETE CASCADE, but the behaviour holds regardless of
    whether the test backend enforces it: resolution only ever considers agents that
    exist, so a dangling grant can never resurrect access."""
    await admin_client.put(
        f"/admin/agents/{agent.id}/grants", json={"user_id": str(plain.id), "tier": "operate"}
    )
    assert (await admin_client.delete(f"/admin/agents/{agent.id}")).status_code == 204

    assert (await plain_client.get("/agents")).json() == []
    assert (await plain_client.get(f"/admin/agents/{agent.id}")).status_code == 404


# --- delegation: a Tier-3 grantee is not a global admin ----------------------


async def test_tier3_grantee_can_administer_only_their_agent(
    admin_client: AsyncClient, plain_client: AsyncClient, agent: Agent, plain: User, db_session
):
    other = Agent(name="not-theirs", status="healthy", access_mode="restricted")
    db_session.add(other)
    await db_session.commit()
    await db_session.refresh(other)

    await admin_client.put(
        f"/admin/agents/{agent.id}/grants", json={"user_id": str(plain.id), "tier": "admin"}
    )

    # They can read and extend the ACL on their agent...
    assert (await plain_client.get(f"/admin/agents/{agent.id}/access")).status_code == 200
    granted = await plain_client.put(
        f"/admin/agents/{agent.id}/grants", json={"user_id": str(plain.id), "tier": "admin"}
    )
    assert granted.status_code == 200
    # ...and nothing on an agent they hold no tier on.
    assert (await plain_client.get(f"/admin/agents/{other.id}/access")).status_code == 404
    # Tier 3 is per-agent, never a fleet-level permission.
    assert (await plain_client.post("/admin/agents/bootstrap")).status_code == 403
    assert (
        await plain_client.post("/admin/agents/elastic", json={"cpu": 2, "memory_gb": 4})
    ).status_code == 403
