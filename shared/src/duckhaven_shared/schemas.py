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
    sampled_at: datetime
