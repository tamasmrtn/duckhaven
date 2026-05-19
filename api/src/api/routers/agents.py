from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_current_user, get_db
from api.models.agent import Agent
from api.models.user import User
from api.schemas.agent import AgentCapabilitiesOut, AgentOut
from api.services.agent_registry import registry

router = APIRouter(prefix="/agents")


@router.get("", response_model=list[AgentOut])
async def list_agents(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[AgentOut]:
    result = await db.execute(select(Agent))
    agents = result.scalars().all()
    connected = registry.connected_ids()
    out = []
    for agent in agents:
        caps = None
        if agent.capabilities:
            caps = AgentCapabilitiesOut(**agent.capabilities)
        status = agent.status
        if str(agent.id) not in connected and status == "healthy":
            status = "unavailable"
        out.append(
            AgentOut(
                id=agent.id,
                name=agent.name,
                status=status,
                capabilities=caps,
                last_ping_at=agent.last_ping_at,
                created_at=agent.created_at,
            )
        )
    return out
