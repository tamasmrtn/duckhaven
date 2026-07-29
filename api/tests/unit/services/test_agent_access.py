"""Per-agent access tier resolution (services.agent_access)."""

import pytest
from conftest import seed_workspace

from api.models.agent import Agent
from api.models.agent_grant import AgentGrant
from api.models.user import User
from api.models.workspace import WorkspaceMember
from api.services.agent_access import (
    agent_tier,
    effective_tier,
    load_caller_grants,
    tier_at_least,
    tier_for_principal,
    tier_rank,
    usable_agents,
    visible_tiers,
)
from api.services.auth import hash_password

# --- pure: the tier ladder ---------------------------------------------------


def test_tiers_are_ordered():
    assert tier_rank("use") < tier_rank("operate") < tier_rank("admin")


def test_tier_at_least_is_inclusive_upward():
    assert tier_at_least("admin", "use")
    assert tier_at_least("operate", "operate")
    assert not tier_at_least("use", "operate")


def test_no_tier_satisfies_nothing():
    assert tier_rank(None) == -1
    assert not tier_at_least(None, "use")


def test_unknown_stored_tier_fails_closed():
    """An unrecognised value ranks below `use`, matching ROLE_ORDER's -1 default —
    a corrupted or future tier must never be read as more access than it is."""
    assert tier_rank("superuser") == -1
    assert not tier_at_least("superuser", "use")


def test_unknown_requirement_is_never_satisfied():
    assert not tier_at_least("admin", "sudo")


# --- pure: effective_tier ----------------------------------------------------


def test_open_mode_floors_an_ungranted_caller_at_use():
    """The behaviour-preserving default: before this ACL any caller could target
    any agent, and an `open` agent keeps that true."""
    assert effective_tier([], "open", is_global_admin=False) == "use"


def test_restricted_mode_hides_an_ungranted_caller():
    assert effective_tier([], "restricted", is_global_admin=False) is None


def test_grant_raises_above_the_open_floor():
    assert effective_tier(["operate"], "open", is_global_admin=False) == "operate"


def test_open_floor_is_a_floor_not_a_fallback():
    """A grant can never leave a caller with *less* than `open` already gave them."""
    assert effective_tier(["use"], "open", is_global_admin=False) == "use"


def test_highest_grant_wins():
    """Grants are additive: a user grant and a workspace grant compose as max()."""
    assert effective_tier(["use", "admin", "operate"], "restricted", is_global_admin=False) == (
        "admin"
    )


def test_global_admin_short_circuits_regardless_of_mode():
    assert effective_tier([], "restricted", is_global_admin=True) == "admin"
    assert effective_tier(["use"], "restricted", is_global_admin=True) == "admin"


def test_unrecognised_access_mode_denies():
    """Anything that is not exactly "open" provides no floor — fail closed."""
    assert effective_tier([], "public", is_global_admin=False) is None


# --- DB-aware ----------------------------------------------------------------


@pytest.fixture
async def member(db_session):
    u = User(email="grantee@access.local", password_hash=hash_password("pw"), name="G", role="user")
    db_session.add(u)
    await db_session.commit()
    await db_session.refresh(u)
    return u


@pytest.fixture
async def superuser(db_session):
    u = User(email="root@access.local", password_hash=hash_password("pw"), name="R", role="admin")
    db_session.add(u)
    await db_session.commit()
    await db_session.refresh(u)
    return u


@pytest.fixture
async def restricted_agent(db_session):
    a = Agent(name="locked", status="healthy", access_mode="restricted")
    db_session.add(a)
    await db_session.commit()
    await db_session.refresh(a)
    return a


async def test_direct_user_grant_resolves(db_session, member, restricted_agent):
    db_session.add(AgentGrant(agent_id=restricted_agent.id, user_id=member.id, tier="operate"))
    await db_session.commit()
    assert await agent_tier(db_session, member, restricted_agent) == "operate"


async def test_workspace_grant_reaches_every_member(db_session, member, restricted_agent):
    ws, _ = await seed_workspace(db_session, user_id=member.id, slug="analytics", role="reader")
    db_session.add(AgentGrant(agent_id=restricted_agent.id, workspace_id=ws.id, tier="use"))
    await db_session.commit()
    assert await agent_tier(db_session, member, restricted_agent) == "use"


async def test_workspace_grant_reaches_any_role_not_just_owners(
    db_session, member, restricted_agent
):
    ws, _ = await seed_workspace(db_session, user_id=member.id, slug="ws-reader", role="reader")
    db_session.add(AgentGrant(agent_id=restricted_agent.id, workspace_id=ws.id, tier="operate"))
    await db_session.commit()
    assert await agent_tier(db_session, member, restricted_agent) == "operate"


async def test_losing_workspace_membership_revokes_the_grant(db_session, member, restricted_agent):
    """The grant is on the workspace, so access follows membership with no ACL edit."""
    ws, _ = await seed_workspace(db_session, user_id=member.id, slug="leaving", role="writer")
    db_session.add(AgentGrant(agent_id=restricted_agent.id, workspace_id=ws.id, tier="use"))
    await db_session.commit()
    assert await agent_tier(db_session, member, restricted_agent) == "use"

    membership = await db_session.get(WorkspaceMember, (ws.id, member.id))
    await db_session.delete(membership)
    await db_session.commit()
    assert await agent_tier(db_session, member, restricted_agent) is None


async def test_user_and_workspace_grants_compose_as_max(db_session, member, restricted_agent):
    ws, _ = await seed_workspace(db_session, user_id=member.id, slug="both", role="reader")
    db_session.add_all(
        [
            AgentGrant(agent_id=restricted_agent.id, workspace_id=ws.id, tier="use"),
            AgentGrant(agent_id=restricted_agent.id, user_id=member.id, tier="admin"),
        ]
    )
    await db_session.commit()
    assert await agent_tier(db_session, member, restricted_agent) == "admin"
    assert (await load_caller_grants(db_session, member.id))[restricted_agent.id] == "admin"


async def test_global_admin_needs_no_grant(db_session, superuser, restricted_agent):
    assert await agent_tier(db_session, superuser, restricted_agent) == "admin"


async def test_ungranted_caller_sees_nothing_on_a_restricted_agent(
    db_session, member, restricted_agent
):
    assert await agent_tier(db_session, member, restricted_agent) is None


async def test_visible_tiers_omits_invisible_agents(db_session, member, restricted_agent):
    shared = Agent(name="shared", status="healthy")
    db_session.add(shared)
    await db_session.commit()
    await db_session.refresh(shared)

    tiers = await visible_tiers(db_session, member, [restricted_agent, shared])
    assert tiers == {shared.id: "use"}


async def test_usable_agents_filters_and_preserves_order(db_session, member, restricted_agent):
    shared = Agent(name="shared2", status="healthy")
    db_session.add(shared)
    await db_session.commit()
    await db_session.refresh(shared)

    usable = await usable_agents(db_session, member.id, [restricted_agent, shared])
    assert [a.id for a in usable] == [shared.id]


async def test_tier_for_principal_denies_a_deleted_principal(db_session, restricted_agent):
    """An unattended run whose owner no longer exists dispatches as nobody, so it
    must resolve to no access rather than falling through."""
    import uuid

    assert await tier_for_principal(db_session, uuid.uuid4(), restricted_agent) is None
