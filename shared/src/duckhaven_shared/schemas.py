from datetime import datetime

from pydantic import BaseModel


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
    sampled_at: datetime
