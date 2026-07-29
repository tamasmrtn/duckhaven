"""Per-agent access tiers — the third authorization axis.

Global RBAC (``users.role`` -> ``role_permissions`` -> ``deps.require_permission``)
answers "may this caller manage agents *at all*", and workspace membership answers
"may this caller act inside this workspace". Neither can say *which* agent, which is
what a fleet shared by capability needs: an elastic agent is matched to demand by
its ``pool_key`` — the set of storage-backend kinds it supports — so one agent
serves every workspace with the same storage shape.

This module is that third axis, and it is an **overlay**, never a replacement:
``Permission.AGENTS_MANAGE`` short-circuits to the top tier on every agent, so a
global admin's access is unchanged and cannot be revoked per-agent.

The tier ladder is ``use < operate < admin``:

- ``use`` — target the agent for queries, SQL sessions and scheduled jobs; read its
  status and monitoring page. No lifecycle operations.
- ``operate`` — everything in ``use``, plus restart, terminate, force disconnect and
  revoking its bootstrap credential.
- ``admin`` — everything in ``operate``, plus deleting the agent, changing its
  access mode, and granting or revoking tiers on it.

Two rules govern resolution, both borrowed from :mod:`api.services.grants`:

**Additive, no deny.** The effective tier is the *maximum* over the caller's direct
grant and the grants on every workspace they belong to. There is no negative grant.

**``open`` mode is a floor, not a ceiling.** Before this ACL existed there was no
``use``-side authorization at all — any authenticated caller could list every agent
and dispatch to it. An agent in ``access_mode="open"`` (the default) preserves that
exactly: an ungranted caller resolves to ``use``. A grant can still raise them
above the floor; it can never push them below it. ``access_mode="restricted"``
removes the floor, so ``use`` needs an explicit grant.

A resolved tier of ``None`` means the agent is invisible: it is filtered out of
every listing, and its routes answer 404 rather than 403 so a denied agent is
indistinguishable from one that does not exist.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from fastapi import HTTPException, status
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.agent import Agent
from api.models.agent_grant import AgentGrant
from api.models.user import User
from api.models.workspace import WorkspaceMember
from api.services.permissions import Permission
from api.services.rbac import has_permission

# Grantable tiers, low -> high. Shaped after ``workspace.ROLE_ORDER`` and
# ``grants.TIER_SCALE`` so a reader recognises the idiom.
AGENT_TIER_ORDER: dict[str, int] = {"use": 0, "operate": 1, "admin": 2}
_TIER_BY_RANK: dict[int, str] = {v: k for k, v in AGENT_TIER_ORDER.items()}

# The highest tier grantable to a *workspace*. `admin` includes granting and
# revoking, and delegating that to "whoever is currently a member of workspace W"
# would make the ACL unauditable — the set of admins would change silently every
# time someone joined. Individual responsibility stays individual.
MAX_WORKSPACE_TIER = "operate"

ACCESS_MODES: frozenset[str] = frozenset({"open", "restricted"})


@dataclass(frozen=True)
class ResolvedAgent:
    """An agent a request resolved to, with the caller's tier on it."""

    agent: Agent
    tier: str


def tier_rank(tier: str | None) -> int:
    """Rank of a tier on the ladder; -1 for None *or* an unrecognised value.

    Fail-closed on an unknown stored tier, matching ``ROLE_ORDER``'s treatment of an
    unknown workspace role.
    """
    if tier is None:
        return -1
    return AGENT_TIER_ORDER.get(tier, -1)


def tier_at_least(tier: str | None, need: str) -> bool:
    """True if ``tier`` satisfies ``need``. An unknown ``need`` is never satisfied."""
    required = AGENT_TIER_ORDER.get(need)
    if required is None:
        return False
    return tier_rank(tier) >= required


def effective_tier(
    granted: Iterable[str], access_mode: str, *, is_global_admin: bool
) -> str | None:
    """The caller's tier on one agent, or None when the agent is invisible to them.

    ``granted`` are the tiers of the grants that reach this caller on this agent
    (their own, plus their workspaces') — already filtered by the query, since
    agents have no grant hierarchy to walk. Pure, so the rules are testable without
    a session.
    """
    if is_global_admin:
        return "admin"
    best = max((tier_rank(t) for t in granted), default=-1)
    if access_mode == "open":
        # The floor. Deliberately a max, not a fallback: a grant raises a caller
        # above `use` on an open agent, and never lowers them below it.
        best = max(best, AGENT_TIER_ORDER["use"])
    if best < 0:
        return None
    return _TIER_BY_RANK[best]


# --- DB-aware wrappers used by the enforcement points -----------------------


async def load_caller_grants(db: AsyncSession, user_id: uuid.UUID) -> dict[uuid.UUID, str]:
    """Every grant reaching ``user_id``, as ``agent_id -> highest tier``.

    One statement: direct user grants unioned with grants on the workspaces the user
    belongs to. This runs on the agent list, the picker, and every dispatch, so it
    must not fan out per agent.
    """
    rows = await db.execute(
        select(AgentGrant.agent_id, AgentGrant.tier).where(
            or_(
                AgentGrant.user_id == user_id,
                AgentGrant.workspace_id.in_(
                    select(WorkspaceMember.workspace_id).where(WorkspaceMember.user_id == user_id)
                ),
            )
        )
    )
    best: dict[uuid.UUID, str] = {}
    for agent_id, tier in rows.all():
        if tier_rank(tier) > tier_rank(best.get(agent_id)):
            best[agent_id] = tier
    return best


async def visible_tiers(
    db: AsyncSession, user: User, agents: Sequence[Agent]
) -> dict[uuid.UUID, str]:
    """``agent_id -> tier`` for each of ``agents`` the caller can see.

    Agents the caller has no access to are absent from the result, so a listing
    filters and annotates in one pass::

        tiers = await visible_tiers(db, user, agents)
        [... for a in agents if a.id in tiers]
    """
    if not agents:
        return {}
    is_admin = await has_permission(db, user, Permission.AGENTS_MANAGE)
    granted = {} if is_admin else await load_caller_grants(db, user.id)
    resolved: dict[uuid.UUID, str] = {}
    for agent in agents:
        tier = effective_tier(
            [granted[agent.id]] if agent.id in granted else [],
            agent.access_mode,
            is_global_admin=is_admin,
        )
        if tier is not None:
            resolved[agent.id] = tier
    return resolved


async def agent_tier(db: AsyncSession, user: User, agent: Agent) -> str | None:
    """The caller's tier on a single agent, or None when it is invisible to them."""
    return (await visible_tiers(db, user, [agent])).get(agent.id)


async def tier_for_principal(db: AsyncSession, principal_id: uuid.UUID, agent: Agent) -> str | None:
    """``agent_tier`` for a principal known only by id (an unattended run's owner).

    A principal that no longer exists has no access — the caller sees None and fails
    the run rather than dispatching as nobody.
    """
    user = await db.get(User, principal_id)
    if user is None:
        return None
    return await agent_tier(db, user, agent)


async def usable_agents(
    db: AsyncSession, principal_id: uuid.UUID, agents: Sequence[Agent]
) -> list[Agent]:
    """Those of ``agents`` the principal may ``use``, order preserved.

    The filter behind agent auto-selection: without it, a caller denied agent A
    could simply omit ``agent_id`` and be routed to A anyway.
    """
    user = await db.get(User, principal_id)
    if user is None:
        return []
    tiers = await visible_tiers(db, user, agents)
    return [a for a in agents if tier_at_least(tiers.get(a.id), "use")]


async def assert_can_assign_agent(db: AsyncSession, user: User, agent_id: uuid.UUID | None) -> None:
    """Require ``use`` on an agent a caller is *binding to future work*.

    The check behind a schedule's ``agent_id`` and a saved query's
    ``default_agent_id``: both name an agent that something will later dispatch to,
    so the person choosing it must be allowed to use it. ``None`` (auto-select) is
    always allowed — the selection is filtered at dispatch instead.
    """
    if agent_id is None:
        return
    agent = await db.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")
    await assert_agent_tier(db, user, agent, "use")


async def assert_agent_tier(db: AsyncSession, user: User, agent: Agent, need: str) -> str:
    """Require ``need`` on ``agent``, returning the caller's actual tier.

    404 when the caller cannot see the agent at all — it should be indistinguishable
    from one that does not exist. 403 when they can see it but lack the tier: they
    already know it exists, so hiding it would only obscure why the action failed.
    """
    tier = await agent_tier(db, user, agent)
    if tier is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")
    if not tier_at_least(tier, need):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": "agent_forbidden",
                "detail": f"This action on agent '{agent.name}' requires the '{need}' tier.",
            },
        )
    return tier
