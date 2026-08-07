import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

# The per-agent access ladder; see api.services.agent_access.
AgentTier = Literal["use", "operate", "admin"]
AgentAccessMode = Literal["open", "restricted"]


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
    # Per-agent query timeout ceiling, in seconds; null = the agent image's default.
    requested_max_timeout_s: float | None = None
    # The *requesting caller's* tier on this agent (use | operate | admin), resolved
    # per request. The server telling the client what it may do beats the client
    # re-deriving it: there is no tier algebra in the UI to drift out of sync. Never
    # null in practice — an agent the caller has no tier on is not returned at all —
    # but optional so a view built outside a request context stays valid.
    access_tier: str | None = None
    # Whether this agent's ACL gates the `use` tier ("restricted") or every
    # authenticated caller may target it ("open").
    access_mode: str = "open"


class ComputeOptionsOut(BaseModel):
    """Ranges + rates the admin UI needs to render the create-compute dialog.

    The UI shows a vCPU slider and a memory slider and computes cost as
    ``cpu * price_vcpu_hour + memory_gb * price_memory_gb_hour``.
    """

    enabled: bool
    provider: str
    # None when the configured provider prices nothing (a container on your own
    # machine); the UI then shows no cost rather than picking a symbol.
    currency: str | None = None
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
    # Query timeout ceiling in seconds, passed to the instance as MAX_TIMEOUT_S;
    # omit to use the agent image's own default (600s). Bounded at 24h — long
    # enough for a genuine large analytical job, not an unbounded runaway query.
    max_timeout_s: float | None = Field(default=None, gt=0, le=86400)
    name: str | None = None
    # Who may use the agent once it registers. Settable here so an agent meant to be
    # reserved is never briefly usable by everyone: it would otherwise be created
    # `open` and only narrowed afterwards from the Access tab, and an agent can
    # register and start taking work in that window. Defaults to `open`, which is
    # how every agent behaved before per-agent access existed.
    access_mode: AgentAccessMode = "open"


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


class PeakQueryPointOut(BaseModel):
    t: datetime
    running: int
    queued: int


class CompletedQueryPointOut(BaseModel):
    t: datetime
    per_minute: float


class ActivityPointOut(BaseModel):
    t: datetime
    # down | starting | query | other | ready | unknown. "unknown" means no lifecycle
    # trail covers this bucket (an agent older than the trail), which is deliberately
    # distinct from "down" — we do not know, rather than knowing it was off.
    state: str


class FailurePointOut(BaseModel):
    t: datetime
    reason: str
    count: int


class UtilizationPointOut(BaseModel):
    t: datetime
    # All null for a bucket the agent reported nothing in, so the chart draws a gap
    # rather than a line through zero it never actually measured.
    cpu_avg: float | None = None
    cpu_max: float | None = None
    mem_avg: float | None = None
    mem_max: float | None = None


class MonitoringSummaryOut(BaseModel):
    uptime_s: int
    # Share of connected time that had query activity; null when never connected.
    busy_ratio: float | None = None
    completed: int
    failed: int
    idle_timeout_minutes: int | None = None


class AgentMonitoringOut(BaseModel):
    """Every series for one agent over one window, on a shared bucket grid."""

    window: str
    bucket_seconds: int
    start: datetime
    end: datetime
    peak_query_count: list[PeakQueryPointOut]
    completed_query_count: list[CompletedQueryPointOut]
    activity: list[ActivityPointOut]
    failures: list[FailurePointOut]
    utilization: list[UtilizationPointOut]
    summary: MonitoringSummaryOut


class BootstrapTokenOut(BaseModel):
    token: str
    expires_at: datetime
    # WebSocket URL the new agent should dial (derived from the request's
    # Host / X-Forwarded-Proto so it Just Works behind a TLS terminator).
    control_plane_url: str
    # Image the agent compose snippet pins to.
    agent_image: str


# --- Per-agent access control -------------------------------------------------


class AgentAccessModeUpdate(BaseModel):
    access_mode: AgentAccessMode


class AgentGrantUpsert(BaseModel):
    """Grant a tier on an agent to exactly one principal — a user or a workspace."""

    user_id: uuid.UUID | None = None
    workspace_id: uuid.UUID | None = None
    tier: AgentTier

    @model_validator(mode="after")
    def _exactly_one_principal(self) -> AgentGrantUpsert:
        # Mirrors the ck_agent_grants_one_principal CHECK, so a bad body is a 422
        # rather than an IntegrityError surfacing as a 500.
        if (self.user_id is None) == (self.workspace_id is None):
            raise ValueError("exactly one of user_id or workspace_id is required")
        # `admin` includes granting, and delegating that to "whoever is currently a
        # member of workspace W" would make the ACL unauditable.
        if self.workspace_id is not None and self.tier == "admin":
            raise ValueError("a workspace grant cannot exceed the 'operate' tier")
        return self


class AgentGrantOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    # Exactly one of these pairs is populated, matching the grant's principal.
    user_id: uuid.UUID | None = None
    user_name: str | None = None
    workspace_id: uuid.UUID | None = None
    workspace_name: str | None = None
    tier: str
    created_at: datetime


class AgentGrantPrincipalOut(BaseModel):
    """A candidate grantee: a user (human or service account) or a workspace."""

    kind: Literal["user", "workspace"]
    id: uuid.UUID
    name: str
    # Users only: their address, to disambiguate people with the same display name.
    email: str | None = None
    is_service_account: bool = False


class AgentAccessOut(BaseModel):
    """Everything the agent's Access tab renders, in one response.

    ``principals`` ships the candidate list alongside the grants so the grant picker
    needs no second call (the ``catalog_grants`` payload does the same).
    """

    agent_id: uuid.UUID
    access_mode: str
    grants: list[AgentGrantOut]
    principals: list[AgentGrantPrincipalOut]
