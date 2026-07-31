"""Request/response shapes for catalog lifecycle (create / attach / list)."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from api.schemas.grant import AccessMode


class CatalogCreate(BaseModel):
    # A catalog has a single identifier-safe name (it is also the slug used in
    # `catalog.schema.table` SQL). Validated against ^[a-z][a-z0-9_]*$ by the
    # service layer.
    name: str = Field(min_length=1, max_length=255)
    # Storage backend for the new catalog. When omitted a bundled object-store
    # backend is auto-provisioned.
    storage_backend_id: uuid.UUID | None = None
    # Access mode of the attachment this call creates. Settable here so a catalog
    # meant to be scoped never exists in an open state: it would otherwise be
    # readable by every workspace member between creation and the operator
    # switching it on the permissions panel. Defaults to `open`.
    access_mode: AccessMode = "open"


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
    storage_backend_name: str
    storage_backend_root_uri: str
    created_at: datetime
    # Set on the workspace-scoped listing: whether this is the workspace's
    # default catalog and whether it is shared with other workspaces.
    is_default: bool = False
    attached_workspaces: int | None = None
    # The attachment's scoped-access mode ("open" | "scoped"); "open" for the
    # deployment-wide listing where there is no single workspace attachment.
    access_mode: str = "open"
