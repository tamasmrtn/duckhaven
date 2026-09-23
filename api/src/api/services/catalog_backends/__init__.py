"""The catalog-metadata seam: one narrow interface over two catalog kinds.

Metadata only. Credential vending, maintenance verbs and storage migration
stay elsewhere because the kinds differ there in substance.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:  # pragma: no cover - typing only
    from sqlalchemy.ext.asyncio import AsyncSession

    from api.models.catalog import Catalog
    from api.models.user import User
    from api.models.workspace import Workspace
    from api.services.polaris import PolarisClient


# --- Errors ------------------------------------------------------------------
# Mirrors PolarisError's shape so both kinds produce identical HTTP responses.


class CatalogBackendError(Exception):
    """A catalog-metadata operation failed."""


class CatalogBackendNotFound(CatalogBackendError):
    """The schema, table or catalog does not exist."""


class CatalogBackendBadRequest(CatalogBackendError):
    """The request was malformed or the metastore rejected it as invalid."""


class CatalogBackendConflict(CatalogBackendError):
    """The object already exists, or a concurrent write lost a race."""


class CatalogBackendUnavailable(CatalogBackendError):
    """The metastore could not be reached, or no agent was available to ask."""


# --- Backend-neutral metadata shapes ----------------------------------------
# Structurally identical to the Polaris response models.


class _Info(BaseModel):
    model_config = ConfigDict(extra="allow")


class CatalogColumnInfo(_Info):
    name: str
    # The catalog's own spelling ("long", "int64"), displayed as-is.
    type_text: str
    type_name: str
    position: int
    nullable: bool = True


class CatalogSchemaInfo(_Info):
    name: str
    catalog_name: str


class CatalogTableInfo(_Info):
    name: str
    catalog_name: str
    schema_name: str
    table_id: str | None = None
    table_type: Literal["MANAGED", "EXTERNAL"] = "MANAGED"
    data_source_format: str = "ICEBERG"
    storage_location: str | None = None
    # Live data bytes when known without a scan: DuckLake only.
    size_bytes: int | None = None
    columns: list[CatalogColumnInfo] = Field(default_factory=list)
    properties: dict[str, str] | None = None
    comment: str | None = None
    format_version: int | None = None
    current_snapshot_summary: dict[str, str] | None = None


class SnapshotInfo(_Info):
    """One snapshot of a table.

    ``granularity="catalog"`` marks a DuckLake catalog-wide commit that touched
    this table.
    """

    snapshot_id: int
    parent_snapshot_id: int | None = None
    timestamp_ms: int
    operation: str | None = None
    summary: dict[str, str] = Field(default_factory=dict)
    schema_id: int | None = None
    is_current: bool = False
    granularity: Literal["table", "catalog"] = "table"


@dataclass(frozen=True)
class CatalogCapabilities:
    """What a catalog kind can do, surfaced so the UI never switches on `kind`."""

    supports_storage_migration: bool = True
    # Whether engines other than DuckDB can read these tables.
    external_engine_readable: bool = True
    # Whether DuckHaven can run maintenance itself. DuckDB's `iceberg` extension
    # implements no maintenance verbs; `ducklake` does.
    supports_maintenance_apply: bool = False
    # Whether data can be copied out into an Iceberg catalog.
    supports_iceberg_export: bool = False
    # Whether schemas and tables can be created with no agent connected.
    supports_agentless_ddl: bool = True
    # Not surfaced for either kind yet; declared so GET /catalog-kinds shows the gap.
    supports_partitioning: bool = False
    supports_sort_order: bool = False
    supports_encryption: bool = False
    supported_storage_kinds: tuple[str, ...] = ("object_store", "s3", "adls_gen2")


@dataclass
class WriteContext:
    """What a DuckLake write needs to dispatch SQL to an agent. Polaris ignores it."""

    workspace: Workspace
    user: User
    db: AsyncSession


class CatalogBackend(Protocol):
    """Catalog-metadata operations, per catalog kind."""

    kind: str

    def capabilities(self) -> CatalogCapabilities: ...

    async def ensure(self, catalog: Catalog) -> None:
        """Make sure the catalog exists and is reachable (self-heal on browse)."""
        ...

    async def provision(self, catalog: Catalog) -> None: ...

    async def deprovision(self, catalog: Catalog) -> None: ...

    async def list_schemas(self, catalog: Catalog) -> list[CatalogSchemaInfo]: ...

    async def create_schema(
        self, catalog: Catalog, name: str, ctx: WriteContext
    ) -> CatalogSchemaInfo: ...

    async def delete_schema(
        self, catalog: Catalog, name: str, ctx: WriteContext, *, cascade: bool = False
    ) -> None:
        """Drop the schema, and with ``cascade`` the tables inside it."""
        ...

    async def list_tables(self, catalog: Catalog, schema: str) -> list[CatalogTableInfo]: ...

    async def get_table(self, catalog: Catalog, schema: str, name: str) -> CatalogTableInfo: ...

    async def create_table(
        self,
        catalog: Catalog,
        schema: str,
        name: str,
        columns: list[Any],
        ctx: WriteContext,
    ) -> CatalogTableInfo: ...

    async def delete_table(
        self, catalog: Catalog, schema: str, name: str, ctx: WriteContext
    ) -> None: ...

    async def list_snapshots(
        self, catalog: Catalog, schema: str, name: str
    ) -> list[SnapshotInfo]: ...


def backend_for(catalog: Catalog, *, polaris: PolarisClient | None = None) -> CatalogBackend:
    """The backend serving ``catalog``, chosen by its kind (imported lazily)."""
    from api.models.catalog import KIND_DUCKLAKE, KIND_ICEBERG_POLARIS

    if catalog.kind == KIND_ICEBERG_POLARIS:
        from api.services.catalog_backends.polaris import PolarisCatalogBackend

        if polaris is None:
            raise CatalogBackendUnavailable(
                "A Polaris client is required to serve an iceberg_polaris catalog"
            )
        return PolarisCatalogBackend(polaris)

    if catalog.kind == KIND_DUCKLAKE:
        from api.services.catalog_backends.ducklake import DuckLakeCatalogBackend

        return DuckLakeCatalogBackend()

    raise CatalogBackendUnavailable(f"Unsupported catalog kind: {catalog.kind!r}")


def capabilities_for(kind: str) -> CatalogCapabilities:
    """Capabilities by kind, for the create flow before a catalog row exists."""
    from api.models.catalog import KIND_DUCKLAKE, KIND_ICEBERG_POLARIS

    if kind == KIND_ICEBERG_POLARIS:
        from api.services.catalog_backends.polaris import POLARIS_CAPABILITIES

        return POLARIS_CAPABILITIES
    if kind == KIND_DUCKLAKE:
        from api.services.catalog_backends.ducklake import DUCKLAKE_CAPABILITIES

        return DUCKLAKE_CAPABILITIES
    raise CatalogBackendUnavailable(f"Unsupported catalog kind: {kind!r}")
