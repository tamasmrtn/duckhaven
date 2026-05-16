from pydantic import BaseModel


class AgentCapabilities(BaseModel):
    duckdb_version: str
    extensions: list[str]
    memory_limit_bytes: int
    host_info: dict[str, str] = {}
