from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class ExportStartRequest(BaseModel):
    # The new Iceberg catalog's name, validated as a slug by the service layer.
    target_name: str = Field(min_length=1, max_length=255)
    target_storage_backend_id: uuid.UUID


class CatalogExportOut(BaseModel):
    id: uuid.UUID
    source_catalog_id: uuid.UUID
    target_catalog_id: uuid.UUID | None = None
    target_name: str
    target_storage_backend_id: uuid.UUID
    workspace_id: uuid.UUID
    status: str
    # The COPY query, for the audit trail.
    query_id: uuid.UUID | None = None
    tables_total: int
    tables_done: int
    error: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
