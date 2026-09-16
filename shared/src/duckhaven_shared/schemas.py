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
    and ``USE``s the active one. ``backend`` is the catalog's storage backend
    descriptor (``{kind, root_uri}``) used to pick the DuckDB IO extension.

    ``kind`` selects how the attach is performed, and which of the two groups of
    fields below is populated:

    - ``iceberg_polaris`` — ``polaris_name`` is the Polaris warehouse. DuckDB
      does the OAuth2 exchange itself and Polaris vends storage credentials on
      access, so the control plane sends none.
    - ``ducklake`` — ``data_path``/``metadata_schema`` locate the catalog, and
      ``meta``/``storage`` carry credentials the control plane minted, because
      DuckLake has no credential vendor of its own.

    Every new field has a default so a newer control plane and an older agent,
    or the reverse, still agree on the frame. ``polaris_name`` is now defaulted
    for the same reason: a DuckLake catalog has no Polaris warehouse to name."""

    slug: str
    backend: dict[str, str | None]
    default_schema: str
    kind: str = "iceberg_polaris"
    polaris_name: str = ""
    # DuckLake only. `meta` is a Postgres connection block and `storage` an
    # object-store credential block; both are vended per dispatch and never
    # persisted on the agent.
    data_path: str | None = None
    metadata_schema: str | None = None
    meta: dict[str, str | int] | None = None
    storage: dict[str, object] | None = None


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
    # EXPLAIN-based estimates abandoned because DuckDB's planner spun and would
    # not stop when interrupted. Each one costs a thread and a core until the
    # agent restarts, so a rising number is worth alerting on. Defaulted for
    # back-compat with older agents.
    estimates_abandoned: int = 0
    sampled_at: datetime
