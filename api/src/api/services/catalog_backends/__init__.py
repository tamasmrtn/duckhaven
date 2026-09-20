"""The catalog-metadata seam: one narrow interface over two catalog kinds.

Schemas, tables, columns and snapshots have the same shape whether they come
from a Polaris REST catalog or a DuckLake catalog, so they sit behind one
interface. Catalog metadata only: credential vending, maintenance verbs and
storage migration stay elsewhere because the kinds differ there in substance.

The ``Protocol`` documents the surface; it is not enforced at runtime.
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
# Structurally identical to the Polaris response models, restated so a DuckLake
# backend is not constructing something called PolarisTable.


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
    # "ICEBERG" or "DUCKLAKE" — what the UI shows as the table format.
    data_source_format: str = "ICEBERG"
    storage_location: str | None = None
    # Total bytes of the table's live data files, when the catalog knows it
    # without a scan. DuckLake keeps a running total; Polaris does not expose
    # one, so it stays None there and the agent probe remains the only source.
    size_bytes: int | None = None
    columns: list[CatalogColumnInfo] = Field(default_factory=list)
    properties: dict[str, str] | None = None
    comment: str | None = None
    # None for DuckLake, which has neither concept.
    format_version: int | None = None
    current_snapshot_summary: dict[str, str] | None = None


class SnapshotInfo(_Info):
    """One snapshot of a table.

    Ids stay ints (the API layer stringifies them for JS). ``granularity``
    distinguishes an Iceberg per-table snapshot from a DuckLake catalog-wide
    commit, for which this is the subset that changed the table.
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
    supported_storage_kinds: tuple[str, ...] = ("object_store", "s3", "adls_gen2")


@dataclass
class WriteContext:
    """What a metadata write needs beyond its arguments.

    Polaris writes are a REST call and ignore this. A DuckLake write runs as SQL
    on an agent, so the caller passes workspace, user and session explicitly.
    """

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
        """Drop the schema, and with ``cascade`` the tables inside it.

        The backend owns how: Polaris must be emptied first, DuckLake does it in
        one statement.
        """
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
    """The backend serving ``catalog``, chosen by its kind.

    Imported lazily per kind, so an unused kind's dependencies stay out of the
    process.
    """
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
