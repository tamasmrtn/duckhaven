import secrets
import uuid
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.config import settings
from api.deps import get_current_user, get_db, require_agent_tier, require_permission
from api.models.agent import Agent
from api.models.user import Credential, User
from api.schemas.agent import (
    AgentMetricsOut,
    AgentMonitoringOut,
    AgentOut,
    BootstrapTokenOut,
    ComputeOptionsOut,
    ElasticAgentCreate,
    MetricsSampleOut,
)
from api.services.agent_access import ResolvedAgent, visible_tiers
from api.services.agent_dispatch import (
    connected_agent_ids,
    disconnect_agent,
    gather_agent_metrics,
)
from api.services.agent_monitoring import DEFAULT_WINDOW, WINDOWS, build_monitoring
from api.services.agent_view import build_agent_out
from api.services.compute import pricing
from api.services.compute import service as compute_service
from api.services.permissions import Permission

router = APIRouter(prefix="/agents")

BOOTSTRAP_TTL = timedelta(hours=24)


@router.get("", response_model=list[AgentOut])
async def list_agents(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[AgentOut]:
    """Every agent the caller can see, annotated with their tier on each.

    No longer gated on ``agents:manage``: a per-agent grantee needs this list to
    reach the agents they hold a tier on. The filtering is the guard — an agent the
    caller resolves no tier on is simply absent.
    """
    result = await db.execute(select(Agent))
    agents = result.scalars().all()
    tiers = await visible_tiers(db, user, agents)
    connected = await connected_agent_ids(db)
    out = []
    for agent in agents:
        if agent.id not in tiers:
            continue
        effective_status = agent.status
        if str(agent.id) in connected and effective_status == "unavailable":
            effective_status = "healthy"
        out.append(build_agent_out(agent, status=effective_status, access_tier=tiers[agent.id]))
    return out


@router.get("/metrics", response_model=list[AgentMetricsOut])
async def list_metrics(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[AgentMetricsOut]:
    """Recent live-utilization samples per connected agent (in-memory ring buffer)."""
    buffers = await gather_agent_metrics(db)
    if not buffers:
        return []
    ids = [uuid.UUID(aid) for aid in buffers]
    result = await db.execute(select(Agent).where(Agent.id.in_(ids)))
    agents = result.scalars().all()
    # Telemetry is as sensitive as the monitoring page it feeds, so it obeys the
    # same visibility rule.
    tiers = await visible_tiers(db, user, agents)
    names = {str(agent.id): agent.name for agent in agents if agent.id in tiers}
    return [
        AgentMetricsOut(
            agent_id=uuid.UUID(aid),
            name=names[aid],
            samples=[MetricsSampleOut(**sample) for sample in samples],
        )
        for aid, samples in buffers.items()
        if aid in names
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


# Must stay below the literal GET paths ("/metrics", "/compute-options"): FastAPI
# matches in declaration order, so a path parameter at the router root declared
# above them would swallow both and try to parse "metrics" as a UUID.
@router.get("/{agent_id}", response_model=AgentOut)
async def get_agent(
    resolved: ResolvedAgent = Depends(require_agent_tier("use")),
    db: AsyncSession = Depends(get_db),
) -> AgentOut:
    """One agent, for its detail page."""
    agent = resolved.agent
    # Same reconciliation as the list: a connected agent whose row still says
    # unavailable has simply not had its status written back yet.
    effective_status = agent.status
    if str(agent.id) in await connected_agent_ids(db) and effective_status == "unavailable":
        effective_status = "healthy"
    return build_agent_out(agent, status=effective_status, access_tier=resolved.tier)


@router.get("/{agent_id}/monitoring", response_model=AgentMonitoringOut)
async def agent_monitoring(
    window: str = DEFAULT_WINDOW,
    resolved: ResolvedAgent = Depends(require_agent_tier("use")),
    db: AsyncSession = Depends(get_db),
) -> AgentMonitoringOut:
    """Every chart on the agent's Monitoring tab, for one time window.

    One response rather than one per chart: the series share a bucket grid, so
    splitting them would let a slow request leave two charts describing different
    stretches of time.
    """
    if window not in WINDOWS:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown window {window!r}; expected one of {', '.join(WINDOWS)}",
        )
    return AgentMonitoringOut(**await build_monitoring(db, resolved.agent, window))


@router.post("/elastic", response_model=AgentOut, status_code=status.HTTP_202_ACCEPTED)
async def create_elastic_agent(
    body: ElasticAgentCreate,
    admin: User = Depends(require_permission(Permission.AGENTS_MANAGE)),
    db: AsyncSession = Depends(get_db),
) -> AgentOut:
    """Provision an elastic agent at a chosen vCPU/memory size (the "new compute"
    action), optionally with a per-agent idle-terminate timeout and access mode.

    Stays on the global ``agents:manage`` rather than a per-agent tier: creating an
    agent is a spend decision about the fleet, and there is no agent yet to hold a
    tier on. ``access_mode`` defaults to ``open``.
    """
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
        db,
        name=name,
        cpu=body.cpu,
        memory_gb=body.memory_gb,
        idle_timeout_s=idle_s,
        access_mode=body.access_mode,
    )
    if agent is None:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"error": "provision_failed", "detail": "Failed to provision the agent."},
        )
    # The caller holds agents:manage, so their tier on anything is `admin`.
    return build_agent_out(agent, status=agent.status, access_tier="admin")


@router.post("/{agent_id}/restart", response_model=AgentOut, status_code=status.HTTP_202_ACCEPTED)
async def restart_elastic_agent(
    resolved: ResolvedAgent = Depends(require_agent_tier("operate")),
    db: AsyncSession = Depends(get_db),
) -> AgentOut:
    """Re-provision a terminated/failed elastic agent, reusing its row + settings."""
    agent = resolved.agent
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
    return build_agent_out(restarted, status=restarted.status, access_tier=resolved.tier)


@router.post("/{agent_id}/terminate", response_model=AgentOut, status_code=status.HTTP_202_ACCEPTED)
async def terminate_elastic_agent(
    resolved: ResolvedAgent = Depends(require_agent_tier("operate")),
    db: AsyncSession = Depends(get_db),
) -> AgentOut:
    """Scale a running/provisioning elastic agent in now (destroy its instance)."""
    agent = resolved.agent
    if agent.provider is None or agent.lifecycle not in ("provisioning", "running"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "not_terminable",
                "detail": "Only a running elastic agent can be terminated.",
            },
        )
    await compute_service.terminate_agent(db, agent, reason="manual")
    return build_agent_out(agent, status=agent.status, access_tier=resolved.tier)


@router.post(
    "/{agent_id}/disconnect", response_model=AgentOut, status_code=status.HTTP_202_ACCEPTED
)
async def force_disconnect_agent(
    resolved: ResolvedAgent = Depends(require_agent_tier("operate")),
    db: AsyncSession = Depends(get_db),
) -> AgentOut:
    """Drop the agent's WebSocket, forcing it to reconnect.

    The lifecycle action that works on *every* agent: restart and terminate are
    elastic-only, so without this a static, operator-run agent had no `operate`
    action at all. The agent dials back in on its own, so this is a nudge for a
    wedged socket, not a teardown -- nothing is destroyed and no credential is
    revoked.
    """
    agent = resolved.agent
    await disconnect_agent(db, agent.id)
    await db.execute(sa.update(Agent).where(Agent.id == agent.id).values(status="unavailable"))
    await db.commit()
    return build_agent_out(agent, status="unavailable", access_tier=resolved.tier)


@router.delete("/{agent_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_agent(
    resolved: ResolvedAgent = Depends(require_agent_tier("admin")),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Permanently remove an agent (terminating a live instance first). Irreversible."""
    await compute_service.delete_agent(db, resolved.agent)


@router.delete("/{agent_id}/credential", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_agent(
    resolved: ResolvedAgent = Depends(require_agent_tier("operate")),
    db: AsyncSession = Depends(get_db),
) -> None:
    agent_id = resolved.agent.id
    await db.execute(
        sa.delete(Credential).where(
            Credential.agent_id == agent_id,
            Credential.kind == "agent_session",
        )
    )
    await db.execute(sa.update(Agent).where(Agent.id == agent_id).values(status="unavailable"))
    await db.commit()
    await disconnect_agent(db, agent_id)
