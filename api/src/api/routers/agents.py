from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_current_user, get_db
from api.models.agent import Agent
from api.models.user import User
from api.schemas.agent import AgentOut
from api.services.agent_dispatch import connected_agent_ids
from api.services.agent_view import build_agent_out

router = APIRouter(prefix="/agents")


@router.get("", response_model=list[AgentOut])
async def list_agents(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[AgentOut]:
    result = await db.execute(select(Agent))
    agents = result.scalars().all()
    connected = await connected_agent_ids(db)
    out = []
    for agent in agents:
        status = agent.status
        if str(agent.id) not in connected and status == "healthy":
            status = "unavailable"
        out.append(build_agent_out(agent, status=status))
    return out
