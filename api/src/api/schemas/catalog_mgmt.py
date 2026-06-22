"""Request/response shapes for catalog lifecycle (create / attach / list)."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class CatalogCreate(BaseModel):
    slug: str = Field(min_length=1, max_length=255)
    name: str = Field(min_length=1, max_length=255)
    # Storage backend for the new catalog. When omitted a bundled object-store
    # backend is auto-provisioned (mirrors workspace creation).
    storage_backend_id: uuid.UUID | None = None


class CatalogAttachRequest(BaseModel):
    catalog_id: uuid.UUID
    make_default: bool = False


class CatalogOut(BaseModel):
    id: uuid.UUID
    slug: str
    name: str
    polaris_name: str
    storage_backend_id: uuid.UUID
    storage_backend_kind: str
    created_at: datetime
    # Set on the workspace-scoped listing: whether this is the workspace's
    # default catalog and whether it is shared with other workspaces.
    is_default: bool = False
    attached_workspaces: int | None = None
