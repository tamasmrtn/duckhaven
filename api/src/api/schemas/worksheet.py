import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

# A worksheet is an editor buffer, not a file store: generous, but bounded so a
# runaway paste cannot turn every autosave into a multi-megabyte write.
SQL_MAX_CHARS = 1_000_000


class WorksheetCreate(BaseModel):
    title: str = Field(default="Untitled", min_length=1, max_length=255)
    sql: str = Field(default="", max_length=SQL_MAX_CHARS)
    agent_id: uuid.UUID | None = None
    catalog: str | None = Field(default=None, max_length=255)
    timeout_s: float | None = Field(default=None, gt=0, le=7200)
    saved_query_id: uuid.UUID | None = None
    is_open: bool = True


class WorksheetUpdate(BaseModel):
    """A partial update. An omitted field is left alone; an explicit null clears it.

    Content (`sql`, `title`) is versioned: send the `version` you last read as
    `base_version`, and a stale one is a 409 rather than a silent overwrite. The
    rest is last-write-wins metadata and needs no version.
    """

    base_version: int | None = Field(default=None, ge=1)
    title: str | None = Field(default=None, min_length=1, max_length=255)
    sql: str | None = Field(default=None, max_length=SQL_MAX_CHARS)
    agent_id: uuid.UUID | None = None
    catalog: str | None = Field(default=None, max_length=255)
    timeout_s: float | None = Field(default=None, gt=0, le=7200)
    saved_query_id: uuid.UUID | None = None
    last_query_id: uuid.UUID | None = None
    is_open: bool | None = None
    tab_position: int | None = Field(default=None, ge=0)


class WorksheetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    workspace_id: uuid.UUID
    owner_id: uuid.UUID
    title: str
    sql: str
    agent_id: uuid.UUID | None
    catalog: str | None
    timeout_s: float | None
    saved_query_id: uuid.UUID | None
    last_query_id: uuid.UUID | None
    is_open: bool
    tab_position: int
    version: int
    created_at: datetime
    updated_at: datetime
