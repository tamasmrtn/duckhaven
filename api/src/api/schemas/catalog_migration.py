"""Request/response shapes for catalog storage-backend migration."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class MigrationStartRequest(BaseModel):
    target_storage_backend_id: uuid.UUID


class CatalogMigrationTableOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    schema_name: str
    table_name: str
    status: str
    bytes_copied: int
    error: str | None = None


class CatalogMigrationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    catalog_id: uuid.UUID
    source_storage_backend_id: uuid.UUID
    target_storage_backend_id: uuid.UUID
    status: str
    tables_total: int
    tables_done: int
    bytes_total: int
    bytes_copied: int
    error: str | None = None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    cutover_at: datetime | None = None
    finished_at: datetime | None = None
    # Populated on the detail endpoint; omitted from the list view.
    tables: list[CatalogMigrationTableOut] | None = None


class CatalogMigrationEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    seq: int
    level: str
    message: str
    created_at: datetime
