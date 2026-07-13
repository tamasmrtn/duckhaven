import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class SqlSessionCreate(BaseModel):
    # Optional explicit compute selection; omit to let the API auto-pick a
    # connected, compatible agent (pick_agent_for).
    agent_id: uuid.UUID | None = None
    # Optional default catalog to USE for unqualified names.
    catalog: str | None = None


class SqlSessionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    status: str
    agent_id: uuid.UUID | None
    active_catalog: str | None
    # Scoped object-storage prefix a load may COPY to/from (dlt staging).
    staging_uri: str | None
    error: str | None
    created_at: datetime
    last_active_at: datetime


class SqlStatementCreate(BaseModel):
    sql: str
    timeout_s: float = 600.0
