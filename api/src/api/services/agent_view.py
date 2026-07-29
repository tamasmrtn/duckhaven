"""Build the API ``AgentOut`` view of an agent, including elastic size + cost.

Shared by the admin and picker agent lists so the elastic fields (provider,
lifecycle, size, hourly cost) are derived one way in one place.

``access_tier`` is the calling principal's tier on the agent, so it is passed in
rather than derived here: it is a property of the *request*, not of the agent.
"""

from __future__ import annotations

from api.models.agent import Agent
from api.schemas.agent import AgentCapabilitiesOut, AgentOut
from api.services.compute import pricing


def build_agent_out(agent: Agent, *, status: str, access_tier: str | None = None) -> AgentOut:
    caps = AgentCapabilitiesOut(**agent.capabilities) if agent.capabilities else None
    cost = None
    if agent.requested_cpu is not None and agent.requested_memory_gb is not None:
        cost = pricing.hourly_cost(agent.requested_cpu, agent.requested_memory_gb)
    idle_minutes = round(agent.idle_timeout_s / 60) if agent.idle_timeout_s is not None else None
    return AgentOut(
        id=agent.id,
        name=agent.name,
        status=status,
        capabilities=caps,
        last_ping_at=agent.last_ping_at,
        created_at=agent.created_at,
        provider=agent.provider,
        lifecycle=agent.lifecycle,
        requested_cpu=agent.requested_cpu,
        requested_memory_gb=agent.requested_memory_gb,
        hourly_cost=cost,
        idle_timeout_minutes=idle_minutes,
        access_tier=access_tier,
        access_mode=agent.access_mode,
    )
