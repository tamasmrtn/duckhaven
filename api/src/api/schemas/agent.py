import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class AgentCapabilitiesOut(BaseModel):
    duckdb_version: str
    extensions: list[str]
    memory_limit_gb: float
    cores: int
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


class BootstrapTokenOut(BaseModel):
    token: str
    expires_at: datetime
