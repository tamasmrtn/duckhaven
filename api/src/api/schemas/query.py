import uuid
from datetime import datetime
from typing import Any

from pydantic import AliasChoices, BaseModel, ConfigDict, Field


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


class ColumnSchemaOut(BaseModel):
    """One result column's name and DuckDB logical type.

    ``type`` is spelled the way DuckDB itself prints a logical type — the same
    string ``DESCRIBE`` returns in its ``column_type`` column. That spelling is
    self-describing and re-parses exactly, including parameterized and nested
    types (``DECIMAL(38,10)``, ``STRUCT(a INTEGER, b VARCHAR)``, ``ENUM('e', 'f')``,
    ``INTEGER[2]``), so no separate precision/scale fields are carried.

    No ``nullable`` field: DuckDB relations carry no reliable nullability
    (``DESCRIBE`` reports ``YES`` unconditionally), so reporting it would be
    inventing data.
    """

    name: str
    type: str


class QueryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    workspace_id: uuid.UUID
    agent_id: uuid.UUID | None
    user_id: uuid.UUID | None = None
    # Display name of the user/service account that ran the query, resolved from
    # user_id for the History view. Null for internal runs with no user.
    user_name: str | None = None
    sql: str
    status: str
    # Tags non-interactive runs (e.g. "scheduled") so the UI can label them.
    origin: str | None = None
    # Set when the run was produced by a schedule; lets the runs feed map a run
    # back to its schedule. Null for interactive runs.
    schedule_id: uuid.UUID | None = None
    row_count: int | None
    duration_ms: int | None
    result_bytes: int | None = None
    error: str | None
    progress: dict[str, Any] | None = None
    # The result's column types, available as soon as the query is done — a client
    # can learn them without fetching a page. Null for DDL/DML and for runs by an
    # agent older than the field. Read off the ORM's `result_schema`; the wire name
    # matches RowsPageOut's so the two surfaces spell the same thing identically.
    column_schema: list[ColumnSchemaOut] | None = Field(
        default=None, validation_alias=AliasChoices("column_schema", "result_schema")
    )
    started_at: datetime
    finished_at: datetime | None


class RowsPageOut(BaseModel):
    rows: list[dict[str, Any]]
    columns: list[str]
    cursor: str | None
    total: int
    # The columns' types, as the executing agent reported them. Additive: `columns`
    # keeps its names-only shape for clients already in the field. Null for DDL/DML
    # and for runs by an agent older than this field. Values are still JSON-encoded,
    # so DECIMAL and HUGEINT arrive as floats regardless of what this says.
    column_schema: list[ColumnSchemaOut] | None = None


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


class ScheduleCreate(BaseModel):
    # v1 only supports "saved_query"; kept explicit as the extension seam.
    job_type: str = "saved_query"
    saved_query_id: uuid.UUID
    agent_id: uuid.UUID | None = None
    cron: str
    enabled: bool = True


class ScheduleUpdate(BaseModel):
    cron: str | None = None
    enabled: bool | None = None
    agent_id: uuid.UUID | None = None


class ScheduleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    workspace_id: uuid.UUID
    job_type: str
    saved_query_id: uuid.UUID | None
    agent_id: uuid.UUID | None
    cron: str
    enabled: bool
    next_run_at: datetime | None
    last_run_at: datetime | None
    last_run_query_id: uuid.UUID | None
    created_at: datetime
