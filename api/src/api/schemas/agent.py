import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class AgentCapabilitiesOut(BaseModel):
    duckdb_version: str
    extensions: list[str]
    memory_limit_gb: float
    cores: int
    cpu_model: str | None = None
    cpu_cores_physical: int | None = None
    tailscale_ip: str | None = None
    host: str | None = None


class AgentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    status: str
    capabilities: AgentCapabilitiesOut | None
    last_ping_at: datetime | None
    created_at: datetime


class MetricsSampleOut(BaseModel):
    cpu_percent: float
    memory_percent: float
    running_queries: int = 0
    queued_queries: int = 0
    active_profile: str = "auto"
    sampled_at: datetime


class AgentMetricsOut(BaseModel):
    agent_id: uuid.UUID
    name: str
    samples: list[MetricsSampleOut]


class BootstrapTokenOut(BaseModel):
    token: str
    expires_at: datetime
    # WebSocket URL the new agent should dial (derived from the request's
    # Host / X-Forwarded-Proto so it Just Works behind a TLS terminator).
    control_plane_url: str
    # Image the agent compose snippet pins to.
    agent_image: str
