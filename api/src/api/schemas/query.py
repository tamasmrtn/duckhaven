import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class QueryCreate(BaseModel):
    sql: str
    agent_id: uuid.UUID
    memory_limit_gb: float = 6.0
    timeout_s: float = 600.0


class QueryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    workspace_id: uuid.UUID
    agent_id: uuid.UUID | None
    user_id: uuid.UUID | None = None
    sql: str
    status: str
    row_count: int | None
    duration_ms: int | None
    error: str | None
    started_at: datetime
    finished_at: datetime | None


class RowsPageOut(BaseModel):
    rows: list[dict[str, Any]]
    columns: list[str]
    cursor: str | None
    total: int


class SavedQueryCreate(BaseModel):
    name: str
    sql: str
    default_agent_id: uuid.UUID | None = None


class SavedQueryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    workspace_id: uuid.UUID
    name: str
    sql: str
    default_agent_id: uuid.UUID | None
    created_by: uuid.UUID
    created_at: datetime
    last_run_at: datetime | None
