from datetime import datetime

from pydantic import BaseModel

from duckhaven_shared.concurrency import DEFAULT_PROFILE


class AgentCapabilities(BaseModel):
    duckdb_version: str
    extensions: list[str]
    memory_limit_gb: float
    cores: int
    cpu_model: str | None = None
    cpu_cores_physical: int | None = None
    tailscale_ip: str | None = None
    host: str | None = None
    # Optional control-plane protocol features this agent implements, letting the
    # API gate behavior on agent version without a version number. Empty for older
    # agents, which is what the absence of a feature means. See
    # api.services.agent_capabilities.
    protocol_features: list[str] = []


class CatalogAttach(BaseModel):
    """One catalog the agent should ATTACH for a dispatched query.

    The control plane sends a list of these (plus an ``active_catalog`` slug) in
    the DISPATCH_QUERY payload; the agent attaches each under its ``slug`` alias
    and ``USE``s the active one. ``polaris_name`` is the Polaris warehouse name;
    ``backend`` is the catalog's storage backend descriptor (``{kind, root_uri}``)
    used to pick the DuckDB IO extension + credential-vending mode."""

    slug: str
    polaris_name: str
    backend: dict[str, str | None]
    default_schema: str


class MetricsSample(BaseModel):
    """A single live-utilization sample pushed by an agent over METRICS_SAMPLE."""

    cpu_percent: float
    memory_percent: float
    # Live admission state: how many queries are running vs waiting in the
    # agent's FIFO queue, and the active concurrency profile (see
    # duckhaven_shared.concurrency). Defaulted for back-compat with older agents.
    running_queries: int = 0
    queued_queries: int = 0
    active_profile: str = DEFAULT_PROFILE
    # Number of open SQL sessions holding a persistent connection (+ admission
    # reservation) on this agent. Defaulted for back-compat with older agents.
    session_count: int = 0
    # Statements parked waiting for budget to grow into rather than running at the
    # idle baseline (see agent.control.channel._resize_for_statement). Distinct
    # from ``queued_queries``, which counts work not yet admitted at all.
    # Defaulted for back-compat with older agents.
    growth_waiting: int = 0
    sampled_at: datetime
