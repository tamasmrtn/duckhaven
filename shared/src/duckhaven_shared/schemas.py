from datetime import datetime
from typing import Literal

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
    # The curated runtime this agent's image was built as (duckhaven_shared.runtimes),
    # or, for an image built before runtimes existed, the one its DuckDB line
    # matches. None when neither applies.
    runtime_id: str | None = None
    # The engine's own `select version()` ("v1.5.5"), which unlike
    # `duckdb_version` is the spelling to derive the DuckDB line from.
    engine_version: str | None = None
    # The DuckHaven release the image was built from.
    agent_version: str | None = None
    # DuckDB's platform string, e.g. "linux_amd64".
    platform: str | None = None
    # Whether the DuckDB configuration lock really applies on this engine:
    # "verified", "failed", or "disabled" by the operator. None from older agents.
    sandbox: Literal["verified", "failed", "disabled"] | None = None


class CatalogAttach(BaseModel):
    """One catalog the agent should ATTACH for a dispatched query.

    The control plane sends a list of these (plus an ``active_catalog`` slug) in
    the DISPATCH_QUERY payload; the agent attaches each under its ``slug`` alias
    and ``USE``s the active one.

    ``iceberg_polaris`` carries a ``polaris_name``; Polaris vends storage
    credentials. ``ducklake`` carries control-plane-minted ``meta``/``storage``
    credentials. New fields are defaulted for older agents.
    """

    slug: str
    backend: dict[str, str | None]
    default_schema: str
    kind: str = "iceberg_polaris"
    polaris_name: str = ""
    # DuckLake only; vended per dispatch, never persisted on the agent.
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
    # Everything below is None from an agent too old to measure it, which the
    # control plane must show as "not measured", never as zero.
    #
    # Wall seconds this sample covers (since the previous one, or since the agent
    # reconnected), so a rollup can tell a full minute from a partial one.
    interval_s: float | None = None
    # Highest memory level reached during the interval, not just at the sample
    # instant: short spikes between two samples are what exhaust memory.
    memory_peak_percent: float | None = None
    # Queries actually running a statement right now. Unlike running_queries, an
    # idle held SQL session (a dbt/BI connection waiting for its next statement) is
    # not counted; it is in idle_sessions instead.
    executing_queries: int | None = None
    idle_sessions: int | None = None
    # OOM kills in the agent's cgroup during the interval, and since the cgroup
    # started. The cumulative values are what Prometheus counters are built from,
    # so a scrape interval can never miss an event.
    oom_kills: int | None = None
    oom_kills_total: int | None = None
    cpu_seconds_total: float | None = None
    sampled_at: datetime
