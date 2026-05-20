from pydantic import BaseModel


class AgentCapabilities(BaseModel):
    duckdb_version: str
    extensions: list[str]
    memory_limit_gb: float
    cores: int
    tailscale_ip: str | None = None
    host: str | None = None
