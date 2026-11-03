"""Per-agent access tier resolution (services.agent_access)."""

import pytest
from conftest import seed_workspace
from fastapi import HTTPException
from sqlalchemy import select

from api.models.agent import Agent
from api.models.agent_grant import AgentGrant
from api.models.user import User
from api.models.workspace import WorkspaceMember
from api.services.agent_access import (
    agent_tier,
    assert_agent_tier,
    assert_agent_use,
    assert_can_assign_agent,
    assert_can_set_default_agent,
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


# --- the workspace default's embedded grant ----------------------------------


async def _set_default(db_session, workspace, agent):
    workspace.default_agent_id = agent.id if agent is not None else None
    await db_session.commit()
    await db_session.refresh(workspace)


async def test_a_member_may_use_the_workspace_default_without_a_grant(
    db_session, member, restricted_agent
):
    ws, _ = await seed_workspace(db_session, user_id=member.id, slug="embed", role="reader")
    await _set_default(db_session, ws, restricted_agent)

    assert await agent_tier(db_session, member, restricted_agent) is None
    assert await assert_agent_use(db_session, member, restricted_agent, ws) == "use"


async def test_a_non_member_gets_nothing_from_the_default(db_session, member, restricted_agent):
    """The grant is for the workspace's members: a route that forgot to assert
    membership must not hand it to an outsider."""
    owner = User(email="o@access.local", password_hash=hash_password("pw"), name="O", role="user")
    db_session.add(owner)
    await db_session.commit()
    ws, _ = await seed_workspace(db_session, user_id=owner.id, slug="private", role="owner")
    await _set_default(db_session, ws, restricted_agent)

    with pytest.raises(HTTPException) as exc:
        await assert_agent_use(db_session, member, restricted_agent, ws)
    assert exc.value.status_code == 404


async def test_the_grant_covers_only_the_default_agent(db_session, member, restricted_agent):
    other = Agent(name="other-locked", status="healthy", access_mode="restricted")
    db_session.add(other)
    await db_session.commit()
    await db_session.refresh(other)
    ws, _ = await seed_workspace(db_session, user_id=member.id, slug="only-one", role="reader")
    await _set_default(db_session, ws, restricted_agent)

    with pytest.raises(HTTPException) as exc:
        await assert_agent_use(db_session, member, other, ws)
    assert exc.value.status_code == 404


async def test_another_workspaces_default_grants_nothing_here(db_session, member, restricted_agent):
    here, _ = await seed_workspace(db_session, user_id=member.id, slug="here", role="reader")
    there, _ = await seed_workspace(db_session, user_id=member.id, slug="there", role="reader")
    await _set_default(db_session, there, restricted_agent)

    with pytest.raises(HTTPException):
        await assert_agent_use(db_session, member, restricted_agent, here)


async def test_the_grant_is_use_only(db_session, member, restricted_agent):
    ws, _ = await seed_workspace(db_session, user_id=member.id, slug="use-only", role="reader")
    await _set_default(db_session, ws, restricted_agent)

    # The lifecycle and admin routes keep asking the ladder, which sees no grant.
    with pytest.raises(HTTPException) as exc:
        await assert_agent_tier(db_session, member, restricted_agent, "operate")
    assert exc.value.status_code == 404


async def test_clearing_the_default_withdraws_the_grant_at_once(
    db_session, member, restricted_agent
):
    ws, _ = await seed_workspace(db_session, user_id=member.id, slug="withdrawn", role="reader")
    await _set_default(db_session, ws, restricted_agent)
    assert await assert_agent_use(db_session, member, restricted_agent, ws) == "use"

    await _set_default(db_session, ws, None)
    with pytest.raises(HTTPException):
        await assert_agent_use(db_session, member, restricted_agent, ws)


async def test_a_real_higher_tier_is_not_downgraded_by_the_embed(
    db_session, member, restricted_agent
):
    ws, _ = await seed_workspace(db_session, user_id=member.id, slug="higher", role="reader")
    await _set_default(db_session, ws, restricted_agent)
    db_session.add(AgentGrant(agent_id=restricted_agent.id, user_id=member.id, tier="operate"))
    await db_session.commit()

    assert await assert_agent_use(db_session, member, restricted_agent, ws) == "operate"


async def test_without_a_workspace_the_old_rule_applies(db_session, member, restricted_agent):
    with pytest.raises(HTTPException) as exc:
        await assert_agent_use(db_session, member, restricted_agent, None)
    assert exc.value.status_code == 404


async def test_a_run_owner_keeps_the_default_only_while_a_member(
    db_session, member, restricted_agent
):
    ws, _ = await seed_workspace(db_session, user_id=member.id, slug="owner-run", role="writer")
    await _set_default(db_session, ws, restricted_agent)
    assert await tier_for_principal(db_session, member.id, restricted_agent, workspace=ws) == "use"

    await db_session.execute(
        WorkspaceMember.__table__.delete().where(WorkspaceMember.user_id == member.id)
    )
    await db_session.commit()
    assert await tier_for_principal(db_session, member.id, restricted_agent, workspace=ws) is None


async def test_binding_work_to_the_default_needs_no_grant(db_session, member, restricted_agent):
    ws, _ = await seed_workspace(db_session, user_id=member.id, slug="bind", role="writer")
    await _set_default(db_session, ws, restricted_agent)

    await assert_can_assign_agent(db_session, member, restricted_agent.id, workspace=ws)
    with pytest.raises(HTTPException):
        await assert_can_assign_agent(db_session, member, restricted_agent.id)


# --- who may make an agent the default ---------------------------------------


async def test_use_is_enough_to_make_an_open_agent_the_default(db_session, member):
    open_agent = Agent(name="open-one", status="healthy")
    db_session.add(open_agent)
    await db_session.commit()
    await db_session.refresh(open_agent)

    agent = await assert_can_set_default_agent(db_session, member, open_agent.id)
    assert agent.id == open_agent.id


async def test_a_restricted_agent_needs_operate_to_become_the_default(
    db_session, member, restricted_agent
):
    db_session.add(AgentGrant(agent_id=restricted_agent.id, user_id=member.id, tier="use"))
    await db_session.commit()
    with pytest.raises(HTTPException) as exc:
        await assert_can_set_default_agent(db_session, member, restricted_agent.id)
    assert exc.value.status_code == 403

    grant = (
        await db_session.execute(select(AgentGrant).where(AgentGrant.user_id == member.id))
    ).scalar_one()
    grant.tier = "operate"
    await db_session.commit()
    assert (
        await assert_can_set_default_agent(db_session, member, restricted_agent.id)
    ).id == restricted_agent.id


async def test_clearing_the_default_needs_nothing_from_the_ladder(db_session, member):
    assert await assert_can_set_default_agent(db_session, member, None) is None
