import secrets
import uuid
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.config import settings
from api.deps import get_db, require_permission
from api.models.agent import Agent
from api.models.user import Credential, User
from api.schemas.agent import (
    AgentMetricsOut,
    AgentOut,
    BootstrapTokenOut,
    ComputeOptionsOut,
    ElasticAgentCreate,
    MetricsSampleOut,
)
from api.services.agent_dispatch import (
    connected_agent_ids,
    disconnect_agent,
    gather_agent_metrics,
)
from api.services.agent_view import build_agent_out
from api.services.compute import pricing
from api.services.compute import service as compute_service
from api.services.permissions import Permission

router = APIRouter(prefix="/agents")

BOOTSTRAP_TTL = timedelta(hours=24)


@router.get("", response_model=list[AgentOut])
async def list_agents(
    admin: User = Depends(require_permission(Permission.AGENTS_MANAGE)),
    db: AsyncSession = Depends(get_db),
) -> list[AgentOut]:
    result = await db.execute(select(Agent))
    agents = result.scalars().all()
    connected = await connected_agent_ids(db)
    out = []
    for agent in agents:
        effective_status = agent.status
        if str(agent.id) in connected and effective_status == "unavailable":
            effective_status = "healthy"
        out.append(build_agent_out(agent, status=effective_status))
    return out


@router.get("/metrics", response_model=list[AgentMetricsOut])
async def list_metrics(
    admin: User = Depends(require_permission(Permission.AGENTS_MANAGE)),
    db: AsyncSession = Depends(get_db),
) -> list[AgentMetricsOut]:
    """Recent live-utilization samples per connected agent (in-memory ring buffer)."""
    buffers = await gather_agent_metrics(db)
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
    admin: User = Depends(require_permission(Permission.AGENTS_MANAGE)),
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


@router.get("/compute-options", response_model=ComputeOptionsOut)
async def compute_options(
    admin: User = Depends(require_permission(Permission.AGENTS_MANAGE)),
) -> ComputeOptionsOut:
    """vCPU/memory ranges + rates for the create-compute dialog's sliders.

    The range comes from the configured platform -- ACI's per-group quota, or the
    host a Docker deployment provisions onto -- so the sliders cannot offer a size
    that would be refused at create.
    """
    lim = await pricing.limits()
    return ComputeOptionsOut(
        enabled=settings.elastic_compute_enabled,
        provider=settings.elastic_provider,
        currency=await pricing.currency(),
        cpu_min=lim.cpu_min,
        cpu_max=lim.cpu_max,
        cpu_step=lim.cpu_step,
        memory_min_gb=lim.memory_min_gb,
        memory_max_gb=lim.memory_max_gb,
        memory_step_gb=lim.memory_step_gb,
        price_vcpu_hour=settings.elastic_azure_price_vcpu_hour,
        price_memory_gb_hour=settings.elastic_azure_price_memory_gb_hour,
        default_idle_minutes=round(settings.elastic_idle_timeout_s / 60),
    )


@router.post("/elastic", response_model=AgentOut, status_code=status.HTTP_202_ACCEPTED)
async def create_elastic_agent(
    body: ElasticAgentCreate,
    admin: User = Depends(require_permission(Permission.AGENTS_MANAGE)),
    db: AsyncSession = Depends(get_db),
) -> AgentOut:
    """Provision an elastic agent at a chosen vCPU/memory size (the "new compute"
    action), optionally with a per-agent idle-terminate timeout."""
    if not settings.elastic_compute_enabled:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error": "elastic_disabled", "detail": "Elastic compute is not enabled."},
        )
    lim = await pricing.limits()
    if not lim.allows(body.cpu, body.memory_gb):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "error": "invalid_size",
                "detail": (
                    f"cpu must be {lim.cpu_min}-{lim.cpu_max} and memory "
                    f"{lim.memory_min_gb}-{lim.memory_max_gb} GB."
                ),
            },
        )
    idle_s = body.idle_timeout_minutes * 60 if body.idle_timeout_minutes else None
    name = body.name or f"elastic-{secrets.token_hex(3)}"
    agent = await compute_service.provision_elastic_agent(
        db, name=name, cpu=body.cpu, memory_gb=body.memory_gb, idle_timeout_s=idle_s
    )
    if agent is None:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"error": "provision_failed", "detail": "Failed to provision the agent."},
        )
    return build_agent_out(agent, status=agent.status)


@router.post("/{agent_id}/restart", response_model=AgentOut, status_code=status.HTTP_202_ACCEPTED)
async def restart_elastic_agent(
    agent_id: uuid.UUID,
    admin: User = Depends(require_permission(Permission.AGENTS_MANAGE)),
    db: AsyncSession = Depends(get_db),
) -> AgentOut:
    """Re-provision a terminated/failed elastic agent, reusing its row + settings."""
    agent = (await db.execute(select(Agent).where(Agent.id == agent_id))).scalar_one_or_none()
    if agent is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    if agent.provider is None or agent.lifecycle not in ("terminated", "failed"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "not_restartable",
                "detail": "Only a terminated elastic agent can be restarted.",
            },
        )
    restarted = await compute_service.restart_elastic_agent(db, agent)
    if restarted is None:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"error": "provision_failed", "detail": "Failed to restart the agent."},
        )
    return build_agent_out(restarted, status=restarted.status)


@router.post("/{agent_id}/terminate", response_model=AgentOut, status_code=status.HTTP_202_ACCEPTED)
async def terminate_elastic_agent(
    agent_id: uuid.UUID,
    admin: User = Depends(require_permission(Permission.AGENTS_MANAGE)),
    db: AsyncSession = Depends(get_db),
) -> AgentOut:
    """Scale a running/provisioning elastic agent in now (destroy its instance)."""
    agent = (await db.execute(select(Agent).where(Agent.id == agent_id))).scalar_one_or_none()
    if agent is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    if agent.provider is None or agent.lifecycle not in ("provisioning", "running"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "not_terminable",
                "detail": "Only a running elastic agent can be terminated.",
            },
        )
    await compute_service.terminate_agent(db, agent, reason="manual")
    return build_agent_out(agent, status=agent.status)


@router.delete("/{agent_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_agent(
    agent_id: uuid.UUID,
    admin: User = Depends(require_permission(Permission.AGENTS_MANAGE)),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Permanently remove an agent (terminating a live instance first). Irreversible."""
    agent = (await db.execute(select(Agent).where(Agent.id == agent_id))).scalar_one_or_none()
    if agent is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    await compute_service.delete_agent(db, agent)


@router.delete("/{agent_id}/credential", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_agent(
    agent_id: uuid.UUID,
    admin: User = Depends(require_permission(Permission.AGENTS_MANAGE)),
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
    await disconnect_agent(db, agent_id)
