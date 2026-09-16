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
    # Where this catalog keeps its metadata. Defaults to Iceberg + Polaris, so an
    # existing client that never sends it gets exactly today's behaviour.
    # "ducklake" requires DUCKLAKE_ENABLED.
    kind: Literal["iceberg_polaris", "ducklake"] = KIND_ICEBERG_POLARIS
    # Storage backend for the new catalog. When omitted a bundled object-store
    # backend is auto-provisioned. Orthogonal to `kind`.
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
    """What a catalog's kind can do, so clients never switch on `kind` itself.

    Adding a third kind then changes one mapping here rather than every place
    the UI asks "is this DuckLake?".
    """

    # "table" for Iceberg; "catalog" for DuckLake, whose snapshots are commits
    # against the whole catalog rather than one table.
    snapshot_granularity: str
    supports_storage_migration: bool
    # Whether DuckDB itself can run this kind's compaction / snapshot expiry.
    maintenance_executable: bool
    # Whether engines other than DuckDB can read these tables. False for
    # DuckLake — the trade-off a user makes when choosing it.
    external_engine_readable: bool
    supported_storage_kinds: list[str]


class CatalogOut(BaseModel):
    id: uuid.UUID
    slug: str
    name: str
    # Where this catalog's metadata lives: "iceberg_polaris" or "ducklake".
    # Orthogonal to storage_backend_kind below — a catalog of either kind can
    # sit on any storage backend.
    kind: str = KIND_ICEBERG_POLARIS
    # Exactly one of these is set, per the catalog's kind: the Polaris warehouse
    # name, or the Postgres schema holding its ducklake_* tables.
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
