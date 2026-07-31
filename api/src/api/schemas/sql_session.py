from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from api.config import settings


class SqlSessionCreate(BaseModel):
    # Optional explicit compute selection; omit to let the API auto-pick a
    # connected, compatible agent (pick_agent_for).
    agent_id: uuid.UUID | None = None
    # Optional default catalog to USE for unqualified names.
    catalog: str | None = None
    # How long the open call may block while compute starts. None takes the server
    # default (sql_session_wait_timeout_s); 0 never blocks and answers immediately
    # with a pending session. Capped by sql_session_max_wait_timeout_s.
    wait_timeout_s: float | None = Field(default=None, ge=0)
    # What to do when the wait expires with the session still waiting on compute.
    # "cancel" (the default) abandons the session row and answers 503 + Retry-After,
    # which is what a client that cannot poll wants; "continue" hands back the
    # pending session with 202 so the client can poll GET /sql/sessions/{id}.
    # Note "cancel" abandons the *row*, never the compute that is starting -- an
    # immediate retry lands on the agent still coming up.
    on_wait_timeout: Literal["cancel", "continue"] = "cancel"

    @model_validator(mode="after")
    def _validate_wait(self) -> SqlSessionCreate:
        if self.wait_timeout_s is not None:
            if self.wait_timeout_s > settings.sql_session_max_wait_timeout_s:
                raise ValueError(
                    f"wait_timeout_s must be at most {settings.sql_session_max_wait_timeout_s}"
                )
            if self.wait_timeout_s == 0 and self.on_wait_timeout == "cancel":
                raise ValueError('wait_timeout_s=0 requires on_wait_timeout="continue"')
        return self


class SqlSessionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    workspace_id: uuid.UUID
    status: str
    agent_id: uuid.UUID | None
    user_id: uuid.UUID | None = None
    active_catalog: str | None
    # Scoped object-storage prefix a load may COPY to/from (dlt staging).
    staging_uri: str | None
    error: str | None
    # Why the session ended: client / idle / max_lifetime / open_timeout /
    # compute_timeout / provisioning_timeout / agent_disconnect / agent_lease /
    # failed. Null while it lives, and on sessions that ended before the field existed.
    close_reason: str | None = None
    # The tool that opened it, from the request's User-Agent.
    client_name: str | None = None
    client_version: str | None = None
    created_at: datetime
    opened_at: datetime | None = None
    last_active_at: datetime
    closed_at: datetime | None = None


class SqlSessionSummaryOut(SqlSessionOut):
    """A session as the audit list renders it: the row plus the joined display
    names and statement count, so the UI needs no follow-up request per row."""

    user_name: str | None = None
    agent_name: str | None = None
    statement_count: int = 0


class SqlStatementCreate(BaseModel):
    sql: str
    timeout_s: float = 600.0


class StagingFilesCreate(BaseModel):
    # File names to stage; each becomes a key under the session's staging prefix.
    files: list[str]

    @field_validator("files")
    @classmethod
    def _validate_files(cls, files: list[str]) -> list[str]:
        if not files:
            raise ValueError("files must not be empty")
        for name in files:
            # A key traversal (slashes / ..) could escape the session's prefix.
            if not name or "/" in name or "\\" in name or ".." in name:
                raise ValueError(f"invalid staging file name: {name!r}")
        return files


class StagedFileOut(BaseModel):
    name: str
    # The assigned object-storage key (s3://… / abfss://…) under the stage.
    key: str
    # Presigned upload (client HTTP PUT) and read (agent httpfs GET) URLs.
    put_url: str
    get_url: str


class StagingFilesOut(BaseModel):
    files: list[StagedFileOut]
    # RFC 3339; applies to every presigned URL in this response.
    expires_at: datetime
