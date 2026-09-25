"""Request/response shapes for catalog lifecycle (create / attach / list)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from api.models.catalog import KIND_ICEBERG_POLARIS
from api.schemas.grant import AccessMode


class CatalogCreate(BaseModel):
    # A catalog has a single identifier-safe name (it is also the slug used in
    # `catalog.schema.table` SQL). Validated against ^[a-z][a-z0-9_]*$ by the
    # service layer.
    name: str = Field(min_length=1, max_length=255)
    # "ducklake" requires DUCKLAKE_ENABLED.
    kind: Literal["iceberg_polaris", "ducklake"] = KIND_ICEBERG_POLARIS
    # Orthogonal to `kind`; omitted means a bundled object-store backend.
    storage_backend_id: uuid.UUID | None = None
    # Access mode of the attachment this call creates. Settable here so a catalog
    # meant to be scoped never exists in an open state: it would otherwise be
    # readable by every workspace member between creation and the operator
    # switching it on the permissions panel. Defaults to `open`.
    access_mode: AccessMode = "open"


class CatalogAttachRequest(BaseModel):
    """Body of the attach PUT. The catalog itself is named in the path."""

    make_default: bool = False


class CatalogCapabilitiesOut(BaseModel):
    """What a catalog's kind can do, so clients never switch on `kind` itself."""

    supports_storage_migration: bool
    external_engine_readable: bool
    supports_maintenance_apply: bool = False
    supports_iceberg_export: bool = False
    supports_agentless_ddl: bool = True
    supported_storage_kinds: list[str]


class CatalogKindOut(BaseModel):
    """One catalog kind this deployment can offer, for the create flow."""

    kind: str
    label: str
    # False when the kind is known but switched off.
    available: bool
    unavailable_reason: str | None = None
    capabilities: CatalogCapabilitiesOut


class CatalogOut(BaseModel):
    id: uuid.UUID
    slug: str
    name: str
    # Orthogonal to storage_backend_kind below.
    kind: str = KIND_ICEBERG_POLARIS
    # Exactly one is set, per kind.
    polaris_name: str | None = None
    metadata_schema: str | None = None
    capabilities: CatalogCapabilitiesOut | None = None
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
