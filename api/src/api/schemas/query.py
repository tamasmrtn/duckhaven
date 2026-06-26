import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class QueryCreate(BaseModel):
    sql: str
    agent_id: uuid.UUID
    timeout_s: float = 600.0
    # When the run originates from a saved query, its id is sent so the backend
    # can stamp the saved query's last_run_at.
    saved_query_id: uuid.UUID | None = None
    # The worksheet's active catalog (slug) — `USE`d for unqualified table names.
    # When omitted the workspace's default catalog is used.
    catalog: str | None = None


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
    result_bytes: int | None = None
    error: str | None
    progress: dict[str, Any] | None = None
    started_at: datetime
    finished_at: datetime | None


class RowsPageOut(BaseModel):
    rows: list[dict[str, Any]]
    columns: list[str]
    cursor: str | None
    total: int


class SqlFunctionOut(BaseModel):
    name: str
    type: str
    return_type: str | None
    signature: str
    examples: str | None = None


class SqlKeywordOut(BaseModel):
    name: str
    category: str | None = None


class SqlTypeOut(BaseModel):
    name: str
    category: str | None = None


class SqlMetadataOut(BaseModel):
    functions: list[SqlFunctionOut]
    keywords: list[SqlKeywordOut]
    types: list[SqlTypeOut]


class SavedQueryCreate(BaseModel):
    name: str
    sql: str
    default_agent_id: uuid.UUID | None = None


class SavedQueryUpdate(BaseModel):
    name: str | None = None
    sql: str | None = None
    default_agent_id: uuid.UUID | None = None


class SavedQueryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    workspace_id: uuid.UUID
    name: str
    sql: str
    default_agent_id: uuid.UUID | None
    created_by: uuid.UUID
    created_by_name: str | None = None
    created_at: datetime
    last_run_at: datetime | None
