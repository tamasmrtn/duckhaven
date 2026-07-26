import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


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
    # Elastic-agent fields; all null for a static, operator-run agent.
    provider: str | None = None
    lifecycle: str | None = None
    requested_cpu: float | None = None
    requested_memory_gb: float | None = None
    # Hourly cost of the provisioned size, computed from the configured rates.
    hourly_cost: float | None = None
    # Per-agent idle scale-in timeout, in minutes; null = the global default.
    idle_timeout_minutes: int | None = None


class ComputeOptionsOut(BaseModel):
    """Ranges + rates the admin UI needs to render the create-compute dialog.

    The UI shows a vCPU slider and a memory slider and computes cost as
    ``cpu * price_vcpu_hour + memory_gb * price_memory_gb_hour``.
    """

    enabled: bool
    provider: str
    currency: str
    cpu_min: float
    cpu_max: float
    cpu_step: float
    memory_min_gb: float
    memory_max_gb: float
    memory_step_gb: float
    price_vcpu_hour: float
    price_memory_gb_hour: float
    default_idle_minutes: int


class ElasticAgentCreate(BaseModel):
    cpu: float
    memory_gb: float
    # Idle scale-in timeout in minutes; omit to use the control plane's default.
    # Bounded because the value is converted to seconds and compared against the idle
    # clock: anything at or below zero makes the reaper terminate the agent on its first
    # tick, seconds after it was asked for. The dialog's min/max are presentation only.
    idle_timeout_minutes: int | None = Field(default=None, ge=1, le=1440)
    name: str | None = None


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
