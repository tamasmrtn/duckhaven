import uuid
from datetime import datetime
from typing import Annotated, Any

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

TAG_KEY = r"^[A-Za-z0-9_.:-]{1,64}$"
MAX_TAGS = 20
TAGS_DESCRIPTION = (
    "Free-form labels for the run, recorded on it and shown in History: at most 20, "
    "keys of letters, digits and `_ . : -` (up to 64), values up to 256 characters."
)
Tags = dict[
    Annotated[str, StringConstraints(pattern=TAG_KEY)],
    Annotated[str, StringConstraints(max_length=256)],
]


def check_tag_count(tags: dict[str, str] | None) -> None:
    if tags is not None and len(tags) > MAX_TAGS:
        raise ValueError(f"at most {MAX_TAGS} tags")


class TypedParameter(BaseModel):
    """A parameter value with the DuckDB type to read it as, for values JSON cannot
    say on its own: a DECIMAL, a DATE, a UUID, a list."""

    model_config = ConfigDict(extra="forbid")

    type: str = Field(
        description=(
            "A DuckDB type: BOOLEAN, an integer type, FLOAT, DOUBLE, DECIMAL(p,s), "
            "VARCHAR, BLOB (value base64-encoded), DATE, TIME, TIMESTAMP, TIMESTAMPTZ, "
            "INTERVAL, UUID or JSON, optionally followed by [] for a list."
        )
    )
    value: Any = Field(description="The value, as a string, number, boolean, null or array.")


ParameterValue = TypedParameter | bool | int | float | str | None

PARAMETERS_DESCRIPTION = (
    "Values for the placeholders in `sql`, rendered into it as typed literals before "
    "it runs: a list for `?`, `?1` or `$1`, an object for `$name`."
)


class QueryCreate(BaseModel):
    sql: str
    # A specific agent (the worksheet engine picker), or omitted to let the server
    # choose: a compatible connected agent on the default runtime first. With
    # elastic compute, a run with none connected parks queued and one is
    # provisioned on demand.
    agent_id: uuid.UUID | None = None
    timeout_s: float = 600.0
    # When the run originates from a saved query, its id is sent so the backend
    # can stamp the saved query's last_run_at.
    saved_query_id: uuid.UUID | None = None
    # The worksheet's active catalog (slug) — `USE`d for unqualified table names.
    # When omitted the workspace's default catalog is used.
    catalog: str | None = None
    # Whether the run may be answered from the result cache when nothing it reads
    # has changed since an identical query ran. False always executes ("re-run
    # without cache"), and the result still refreshes the cache.
    use_cache: bool = True
    parameters: list[ParameterValue] | dict[str, ParameterValue] | None = Field(
        default=None, description=PARAMETERS_DESCRIPTION
    )
    # How long the call may block waiting for the run to finish. Omitted, it never
    # blocks and always answers 202, as before the field existed; sent, the answer
    # is 200 when the run finished within it and 202 otherwise. A run that outlasts
    # the wait keeps running and is polled as usual. Capped by
    # sql_statement_max_wait_timeout_s, the same budget SQL statements have.
    wait_timeout_s: float | None = Field(default=None, ge=0)
    tags: Tags | None = Field(default=None, description=TAGS_DESCRIPTION)

    @model_validator(mode="after")
    def _validate_wait(self) -> QueryCreate:
        from api.config import settings

        check_tag_count(self.tags)

        if (
            self.wait_timeout_s is not None
            and self.wait_timeout_s > settings.sql_statement_max_wait_timeout_s
        ):
            raise ValueError(
                f"wait_timeout_s must be at most {settings.sql_statement_max_wait_timeout_s}"
            )
        return self


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


class DescribeCreate(BaseModel):
    sql: str
    parameters: list[ParameterValue] | dict[str, ParameterValue] | None = Field(
        default=None,
        description=(
            PARAMETERS_DESCRIPTION + " Placeholders without a value are described as "
            "NULL, which DuckDB types as INTEGER where nothing else decides it."
        ),
    )
    # The catalog unqualified names resolve in; the workspace default when omitted.
    catalog: str | None = None
    # A specific agent, or omitted to use any connected one the caller may use (or,
    # with elastic compute, to start one).
    agent_id: uuid.UUID | None = None
    # How long to wait for an agent to answer, starting compute if needed. Server
    # default sql_session_wait_timeout_s; capped by sql_session_max_wait_timeout_s.
    wait_timeout_s: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _validate_wait(self) -> DescribeCreate:
        from api.config import settings

        if (
            self.wait_timeout_s is not None
            and self.wait_timeout_s > settings.sql_session_max_wait_timeout_s
        ):
            raise ValueError(
                f"wait_timeout_s must be at most {settings.sql_session_max_wait_timeout_s}"
            )
        return self


class DescribeOut(BaseModel):
    # Coarse kind of statement ("select", "insert", …), as History classifies it.
    statement_type: str | None
    # The result's columns and DuckDB types, without running the query. Null for a
    # statement that returns no result grid (DML, DDL).
    columns: list[ColumnSchemaOut] | None
    # The placeholders the statement uses, in order: "1", "2", … or the names.
    parameter_names: list[str]


class RowsPageOut(BaseModel):
    rows: list[dict[str, Any]]
    columns: list[str]
    cursor: str | None
    total: int
    # The columns' types, as the executing agent reported them. Additive: `columns`
    # keeps its names-only shape for clients already in the field. Null for DDL/DML
    # and for runs by an agent older than this field. Values of DECIMAL and every
    # 64-bit-or-wider integer type arrive as JSON strings, which keep them exact.
    column_schema: list[ColumnSchemaOut] | None = None


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
    # Coarse kind of statement ("select", "insert", …), classified when the row
    # was written. Null means unknown — the statement did not parse, or the run
    # predates the column. Not the same as "other", which means "parsed, nothing
    # more specific fits".
    statement_type: str | None = None
    # Set when the run was produced by a schedule; lets the runs feed map a run
    # back to its schedule. Null for interactive runs.
    schedule_id: uuid.UUID | None = None
    # Set when this row is a statement run inside a SQL session; lets History link
    # a statement back to the workload it belonged to. Null for interactive and
    # scheduled runs.
    session_id: uuid.UUID | None = None
    # The tool that sent the run, from its User-Agent (a session statement takes
    # its session's). Null for the web app and for runs the platform starts.
    client_name: str | None = None
    client_version: str | None = None
    # Labels the caller attached to the run.
    tags: dict[str, str] | None = None
    # The runtime (DuckDB line, e.g. "1.5") of the agent that ran it, recorded at
    # dispatch. Null before dispatch and for runs older than the field.
    runtime_id: str | None = None
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
    # The first page of rows, when the caller asked for it on a statement submit
    # (`first_page_limit`). Null everywhere else, including every list endpoint that
    # returns QueryOut -- it is only ever populated by the statement route, and only
    # for a query that produced a result file.
    #
    # It exists to save a round trip: a client that has the first page does not need
    # to call GET /queries/{id}/rows at all, which is otherwise mandatory just to
    # learn the result's column names.
    first_page: RowsPageOut | None = None
    # Whether a finished query has a result grid to read: false for DDL/DML, so a
    # client knows not to ask for rows. Null until the query is done.
    has_result: bool | None = None
    # What the result cache did with this run: "hit" (answered from an earlier run's
    # result; nothing executed), "miss" (executed), "bypass" (the cache was off or
    # could not decide in time) or "ineligible" (the query can never be cached).
    # Null when the cache never looked, and for runs older than the cache.
    cache_status: str | None = None
    # Why, when the outcome needs a reason: e.g. "volatile_function", "opted_out".
    cache_detail: str | None = None
    # For a hit: the run whose result was served (and whose profile describes it).
    result_source_query_id: uuid.UUID | None = None
    started_at: datetime
    # When the agent admitted the run and began executing it. With started_at
    # (submission) this splits the run's wall-clock into queue wait and execution,
    # which is what the history table shows on hover. Null for a run that never
    # started, and for rows written before it was recorded.
    running_at: datetime | None = None
    finished_at: datetime | None


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


# Names are compared case-insensitively and must stay unique per workspace, so
# surrounding whitespace is dropped rather than letting "report " shadow "report".
SavedQueryName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)
]


class SavedQueryCreate(BaseModel):
    name: SavedQueryName
    sql: str
    default_agent_id: uuid.UUID | None = None


class SavedQueryUpdate(BaseModel):
    name: SavedQueryName | None = None
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
    # The last change to the SQL, name or default agent, and who made it. The
    # editor is the principal a scheduled run of this query executes as.
    updated_at: datetime
    updated_by: uuid.UUID
    updated_by_name: str | None = None
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
