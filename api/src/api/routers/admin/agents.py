import secrets
import uuid
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.config import settings
from api.deps import get_admin_user, get_db
from api.models.agent import Agent
from api.models.user import Credential, User
from api.schemas.agent import (
    AgentCapabilitiesOut,
    AgentMetricsOut,
    AgentOut,
    BootstrapTokenOut,
    MetricsSampleOut,
)
from api.services.agent_registry import registry

router = APIRouter(prefix="/agents")

BOOTSTRAP_TTL = timedelta(hours=24)


@router.get("", response_model=list[AgentOut])
async def list_agents(
    admin: User = Depends(get_admin_user),
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
        effective_status = agent.status
        if str(agent.id) in connected and effective_status == "unavailable":
            effective_status = "healthy"
        out.append(
            AgentOut(
                id=agent.id,
                name=agent.name,
                status=effective_status,
                capabilities=caps,
                last_ping_at=agent.last_ping_at,
                created_at=agent.created_at,
            )
        )
    return out


@router.get("/metrics", response_model=list[AgentMetricsOut])
async def list_metrics(
    admin: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
) -> list[AgentMetricsOut]:
    """Recent live-utilization samples per connected agent (in-memory ring buffer)."""
    buffers = registry.recent_metrics()
    if not buffers:
        return []
    ids = [uuid.UUID(aid) for aid in buffers]
    result = await db.execute(select(Agent).where(Agent.id.in_(ids)))
    names = {str(agent.id): agent.name for agent in result.scalars().all()}
    return [
        AgentMetricsOut(
            agent_id=uuid.UUID(aid),
            name=names.get(aid, aid),
            samples=[MetricsSampleOut(**sample) for sample in samples],
        )
        for aid, samples in buffers.items()
    ]


def _agent_dial_url(request: Request) -> str:
    """WebSocket URL the new agent should dial.

    Prefers X-Forwarded-* headers so it Just Works behind a TLS reverse proxy;
    falls back to the request's own scheme + Host header for direct deployments.
    """
    forwarded_proto = request.headers.get("x-forwarded-proto")
    forwarded_host = request.headers.get("x-forwarded-host")
    scheme = (forwarded_proto or request.url.scheme).split(",", 1)[0].strip()
    host = (
        (forwarded_host or request.headers.get("host") or request.url.netloc)
        .split(",", 1)[0]
        .strip()
    )
    ws_scheme = "wss" if scheme == "https" else "ws"
    return f"{ws_scheme}://{host}/agents/connect"


@router.post("/bootstrap", response_model=BootstrapTokenOut)
async def bootstrap(
    request: Request,
    admin: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
) -> BootstrapTokenOut:
    token = f"dh_boot_{secrets.token_urlsafe(16)}"
    expires_at = datetime.now(tz=UTC) + BOOTSTRAP_TTL
    cred = Credential(
        user_id=None,
        agent_id=None,
        kind="agent_bootstrap",
        token=token,
        expires_at=expires_at,
    )
    db.add(cred)
    await db.commit()
    return BootstrapTokenOut(
        token=token,
        expires_at=expires_at,
        control_plane_url=_agent_dial_url(request),
        agent_image=settings.agent_image,
    )


@router.delete("/{agent_id}/credential", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_agent(
    agent_id: uuid.UUID,
    admin: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    result = await db.execute(select(Agent).where(Agent.id == agent_id))
    agent = result.scalar_one_or_none()
    if agent is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    await db.execute(
        sa.delete(Credential).where(
            Credential.agent_id == agent_id,
            Credential.kind == "agent_session",
        )
    )
    await db.execute(sa.update(Agent).where(Agent.id == agent_id).values(status="unavailable"))
    await db.commit()
    registry.unregister(agent_id)
