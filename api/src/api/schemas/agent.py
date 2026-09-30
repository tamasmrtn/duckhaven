import uuid
from datetime import date, datetime
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
    protocol_features: list[str] = []
    # What the agent reports about its runtime (see duckhaven_shared.schemas);
    # all null for an agent image built before runtimes existed.
    runtime_id: str | None = None
    engine_version: str | None = None
    agent_version: str | None = None
    platform: str | None = None
    sandbox: Literal["verified", "failed", "disabled"] | None = None


RuntimeStatus = Literal["beta", "ga", "deprecated", "retired"]


class RuntimeOut(BaseModel):
    """One curated agent runtime: a DuckDB line plus its baked extensions."""

    id: str
    display_name: str
    duckdb_line: str
    status: RuntimeStatus
    extensions: list[str]
    ducklake_format: str | None
    upstream_eol: date | None
    # Whether auto-provisioned compute runs this runtime in this deployment.
    default: bool


class AgentRuntimeOut(BaseModel):
    """The runtime an agent runs, as the control plane judges it.

    ``state`` says whether it is trusted with work: ``ok`` and ``inferred`` (an
    image from before runtimes, matched by its DuckDB line) are; ``mismatch``,
    ``unrecognized`` and ``retired`` are shown but refused; ``pending`` means the
    agent hasn't reported yet (for elastic compute, ``id`` is then the runtime it
    was started as).
    """

    id: str | None
    display_name: str | None
    status: RuntimeStatus | None
    state: Literal["pending", "ok", "inferred", "mismatch", "unrecognized", "retired"]
    # Whether it is the deployment's default runtime, which the server prefers when
    # it picks an agent itself; clients choosing a fallback rank the same way.
    default: bool = False


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
    runtime: AgentRuntimeOut | None = None


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
    # Runtimes new compute may be created on (retired and deprecated ones are not
    # offered), and the one to preselect.
    runtimes: list[RuntimeOut] = []
    default_runtime: str | None = None


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
    # The runtime to run; omit for the deployment's default. A beta runtime needs
    # `allow_beta`, so nobody lands on a pre-release DuckDB by accident.
    runtime_id: str | None = None
    allow_beta: bool = False


class MetricsSampleOut(BaseModel):
    cpu_percent: float
    memory_percent: float
    running_queries: int = 0
    queued_queries: int = 0
    active_profile: str = "auto"
    session_count: int = 0
    growth_waiting: int = 0
    # None from an agent too old to measure them (see duckhaven_shared MetricsSample).
    memory_peak_percent: float | None = None
    executing_queries: int | None = None
    idle_sessions: int | None = None
    oom_kills: int | None = None
    interval_s: float | None = None
    sampled_at: datetime


class AgentMetricsOut(BaseModel):
    agent_id: uuid.UUID
    name: str
    samples: list[MetricsSampleOut]


class MonitoringBucketOut(BaseModel):
    """One bucket of every series. Flat, so all charts index the same row."""

    t: datetime
    # Length of the bucket in seconds; shorter than bucket_seconds for the last one
    # when the range ends inside it (``partial``), e.g. the minute in progress now.
    seconds: float
    partial: bool
    # Where the agent's time went. These sum to ``seconds``. "unknown" means no
    # lifecycle record covers it, which is deliberately distinct from "down".
    busy_s: float
    idle_s: float
    starting_s: float
    down_s: float
    unknown_s: float
    # Average number of queries in each state over the bucket (query-seconds divided
    # by bucket seconds). Additive, so they can be stacked.
    running_avg: float
    queued_avg: float
    compute_wait_avg: float
    # Most queries running at any single instant in the bucket.
    peak_running: int
    # Queries that finished in the bucket, by outcome; failures by classified cause.
    done: int
    cancelled: int
    failed: dict[str, int]
    # Nearest-rank p95 of how long queries that started running in the bucket had
    # waited, and how many there were. Null when none started.
    wait_p95_ms: int | None = None
    wait_n: int
    # Sampled resources. Null when the agent reported nothing in the bucket, so the
    # chart draws a gap rather than a line through a zero never measured.
    cpu_avg: float | None = None
    cpu_max: float | None = None
    mem_avg: float | None = None
    mem_max: float | None = None
    oom_kills: int | None = None
    # Share of the bucket the samples actually cover; null from older agents.
    coverage: float | None = None


class MonitoringSpanOut(BaseModel):
    start: datetime
    end: datetime
    # up | starting | down | unknown
    state: str


class MonitoringSummaryOut(BaseModel):
    uptime_s: int
    busy_s: int
    idle_s: int
    # Share of up time with at least one query running; null when never up.
    busy_ratio: float | None = None
    finished: int
    failed: int
    cancelled: int
    failed_by_reason: dict[str, int]
    wait_p95_ms: int | None = None
    wait_n: int
    peak_running: int
    cpu_peak: float | None = None
    mem_peak: float | None = None
    # The newest moment the resource series describe; null when none reported.
    resources_as_of: datetime | None = None


class AgentMonitoringOut(BaseModel):
    """Every series for one agent over one range, on a shared bucket grid."""

    # The preset asked for, or null for a custom (zoomed) range.
    preset: str | None = None
    range_start: datetime
    range_end: datetime
    bucket_seconds: int
    generated_at: datetime
    buckets: list[MonitoringBucketOut]
    spans: list[MonitoringSpanOut]
    summary: MonitoringSummaryOut


class BootstrapTokenOut(BaseModel):
    token: str
    expires_at: datetime
    # WebSocket URL the new agent should dial (derived from the request's
    # Host / X-Forwarded-Proto so it Just Works behind a TLS terminator).
    control_plane_url: str
    # Image the agent compose snippet pins to: the requested runtime's.
    agent_image: str
    runtime_id: str


class BootstrapCreate(BaseModel):
    # The runtime the new agent should run; omit for the deployment's default.
    runtime_id: str | None = None


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
