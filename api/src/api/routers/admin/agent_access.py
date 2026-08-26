"""Manage one agent's access mode and its grants.

The administration surface for :mod:`api.services.agent_access`. Gated on the
``admin`` tier of the agent being administered, which global ``agents:manage``
always satisfies — so a Tier-3 grantee can delegate access to the agents they
administer without being made a fleet-wide admin. That is the point of the tier.

Kept out of ``routers/admin/agents.py`` for the same reason ``routers/grants.py``
is separate from the catalog routes: ACL management is its own concern, and the
agent router is already long.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_current_user, get_db, require_agent_tier
from api.models.agent import Agent
from api.models.agent_grant import AgentGrant
from api.models.user import User
from api.models.workspace import Workspace
from api.routers.admin.service_accounts import SERVICE_ACCOUNT_PROVIDER
from api.schemas.agent import (
    AgentAccessModeUpdate,
    AgentAccessOut,
    AgentGrantOut,
    AgentGrantPrincipalOut,
)
from api.schemas.agent import AgentGrantUpsert as GrantUpsert
from api.services.agent_access import ResolvedAgent

router = APIRouter(prefix="/agents")


async def _payload(db: AsyncSession, agent: Agent) -> AgentAccessOut:
    """The Access tab's whole state: mode, grants, and the candidate grantees.

    Candidates ship alongside the grants so the picker needs no second call, the
    same shape ``routers/grants.py::_payload`` uses. Unlike catalog grants the
    candidate set is *not* narrowed to one workspace's members — an agent is not
    workspace-scoped, so any user or workspace is a legitimate grantee.
    """
    grant_rows = (
        await db.execute(
            select(AgentGrant, User.name, Workspace.name)
            .outerjoin(User, AgentGrant.user_id == User.id)
            .outerjoin(Workspace, AgentGrant.workspace_id == Workspace.id)
            .where(AgentGrant.agent_id == agent.id)
            .order_by(AgentGrant.created_at)
        )
    ).all()
    grants = [
        AgentGrantOut.model_validate(g).model_copy(
            update={"user_name": user_name, "workspace_name": ws_name}
        )
        for g, user_name, ws_name in grant_rows
    ]

    users = (
        (await db.execute(select(User).where(User.is_active).order_by(User.name))).scalars().all()
    )
    workspaces = (await db.execute(select(Workspace).order_by(Workspace.name))).scalars().all()
    principals = [
        AgentGrantPrincipalOut(
            kind="user",
            id=u.id,
            name=u.name,
            email=u.email,
            is_service_account=u.auth_provider == SERVICE_ACCOUNT_PROVIDER,
        )
        for u in users
    ] + [AgentGrantPrincipalOut(kind="workspace", id=w.id, name=w.name) for w in workspaces]
    return AgentAccessOut(
        agent_id=agent.id,
        access_mode=agent.access_mode,
        grants=grants,
        principals=principals,
    )


@router.get("/{agent_id}/access", response_model=AgentAccessOut)
async def get_agent_access(
    resolved: ResolvedAgent = Depends(require_agent_tier("admin")),
    db: AsyncSession = Depends(get_db),
) -> AgentAccessOut:
    """An agent's access policy: its mode, its grants, and the principals named.

    Needs the `admin` tier on the agent -- who may use a machine is itself
    sensitive."""
    return await _payload(db, resolved.agent)


@router.patch("/{agent_id}/access-mode", response_model=AgentAccessOut)
async def set_agent_access_mode(
    body: AgentAccessModeUpdate,
    resolved: ResolvedAgent = Depends(require_agent_tier("admin")),
    db: AsyncSession = Depends(get_db),
) -> AgentAccessOut:
    """Toggle between ``open`` (any authenticated caller may target the agent) and
    ``restricted`` (the ``use`` tier needs an explicit grant).

    Only the ``use`` tier is affected: ``operate`` and ``admin`` always require a
    grant or global ``agents:manage``, in either mode.
    """
    agent = resolved.agent
    await db.execute(update(Agent).where(Agent.id == agent.id).values(access_mode=body.access_mode))
    await db.commit()
    await db.refresh(agent)
    return await _payload(db, agent)


@router.put(
    "/{agent_id}/grants",
    response_model=AgentGrantOut,
    responses={
        200: {"description": "The principal already had a grant here; its tier was replaced."},
        201: {"description": "A new grant was created for this principal."},
    },
)
async def upsert_agent_grant(
    body: GrantUpsert,
    response: Response,
    resolved: ResolvedAgent = Depends(require_agent_tier("admin")),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> AgentGrantOut:
    """Grant a tier to a user or a workspace, replacing that principal's existing
    grant on this agent if it has one."""
    agent = resolved.agent

    # Resolve the principal first so a typo'd id is a 422 rather than a dangling FK
    # that only fails at commit.
    if body.user_id is not None:
        target_user = await db.get(User, body.user_id)
        if target_user is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="No such user.",
            )
        where = (AgentGrant.user_id == body.user_id,)
    else:
        target_ws = await db.get(Workspace, body.workspace_id)
        if target_ws is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="No such workspace.",
            )
        where = (AgentGrant.workspace_id == body.workspace_id,)

    existing = (
        await db.execute(select(AgentGrant).where(AgentGrant.agent_id == agent.id, *where))
    ).scalar_one_or_none()
    if existing is not None:
        existing.tier = body.tier
        grant = existing
    else:
        grant = AgentGrant(
            agent_id=agent.id,
            user_id=body.user_id,
            workspace_id=body.workspace_id,
            tier=body.tier,
            created_by=user.id,
        )
        db.add(grant)
        response.status_code = status.HTTP_201_CREATED
    await db.commit()
    await db.refresh(grant)

    user_name = (
        await db.execute(select(User.name).where(User.id == grant.user_id))
    ).scalar_one_or_none()
    ws_name = (
        await db.execute(select(Workspace.name).where(Workspace.id == grant.workspace_id))
    ).scalar_one_or_none()
    return AgentGrantOut.model_validate(grant).model_copy(
        update={"user_name": user_name, "workspace_name": ws_name}
    )


@router.delete("/{agent_id}/grants/{grant_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_agent_grant(
    grant_id: uuid.UUID,
    resolved: ResolvedAgent = Depends(require_agent_tier("admin")),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Revoke a principal's grant on this agent.

    In `open` mode the principal keeps the `use` tier, which needs no grant; in
    `restricted` mode this removes their access. Queries already running are not
    cancelled."""
    grant = (
        await db.execute(
            select(AgentGrant).where(
                AgentGrant.id == grant_id, AgentGrant.agent_id == resolved.agent.id
            )
        )
    ).scalar_one_or_none()
    if grant is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Grant not found")
    await db.delete(grant)
    await db.commit()
