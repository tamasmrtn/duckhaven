from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_current_user, get_db
from api.models.agent import Agent
from api.models.user import User
from api.schemas.agent import AgentOut, RuntimeOut
from api.services.agent_access import visible_tiers
from api.services.agent_dispatch import connected_agent_ids
from api.services.agent_view import build_agent_out, build_runtime_catalog_out, effective_status
from duckhaven_shared.runtimes import RUNTIMES

router = APIRouter()


@router.get("/agents", response_model=list[AgentOut])
async def list_usable_agents(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[AgentOut]:
    """The agents this caller may target — the engine picker's list.

    Filtered by the per-agent ACL, so omitting ``agent_id`` on a dispatch can never
    reach an agent this list would have hidden. Each row carries the caller's
    ``access_tier`` so the UI can gate lifecycle controls without its own rules.
    """
    result = await db.execute(select(Agent))
    agents = result.scalars().all()
    tiers = await visible_tiers(db, user, agents)
    connected = await connected_agent_ids(db)
    out = []
    for agent in agents:
        if agent.id not in tiers:
            continue
        out.append(
            build_agent_out(
                agent, status=effective_status(agent, connected), access_tier=tiers[agent.id]
            )
        )
    return out


@router.get("/runtimes", response_model=list[RuntimeOut])
async def list_runtimes(user: User = Depends(get_current_user)) -> list[RuntimeOut]:
    """The curated agent runtimes: each one's DuckDB line, baked extensions and
    lifecycle status, and which is this deployment's default.

    Every one this release knows, retired ones included, so an agent still running
    one can be labelled. Which may be chosen for new compute is in
    ``/admin/agents/compute-options``.
    """
    return [build_runtime_catalog_out(r) for r in RUNTIMES.values()]
