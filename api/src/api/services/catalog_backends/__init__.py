"""The catalog-metadata seam: one narrow interface over two catalog kinds.

DuckHaven asks the same questions of every catalog — what schemas are in it,
what tables are in a schema, what columns and snapshots does a table have,
create this schema, drop that table. Those questions have the same shape whether
the answer comes from an Apache Polaris REST catalog or from a DuckLake catalog's
``ducklake_*`` tables, so they belong behind one interface.

Catalog metadata only. Credential vending lives in
``services/session_credentials.py``, maintenance verbs in
``services/maintenance/recommend.py``, and storage migration in
``services/migration/`` — each because the two kinds differ there in substance
rather than in spelling. A method here for anything that is not catalog metadata
is the signal this is drifting.

Shape follows the house registry pattern (``services/compute/backends.py``,
``services/lineage/providers``). The ``Protocol`` is documentation of the whole
surface in one place; it is not enforced at runtime or by a type checker.
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
# Structurally these match the Polaris response models, because those were
# already format-neutral (a name, columns, properties, a storage location).
# They are restated here rather than aliased so that a DuckLake backend is not
# constructing something called PolarisTable.


class _Info(BaseModel):
    model_config = ConfigDict(extra="allow")


class CatalogColumnInfo(_Info):
    name: str
    # The catalog's own type spelling — Iceberg's ("long", "decimal(10,2)") or
    # DuckLake's ("int64"). Displayed as-is; not normalized across kinds.
    type_text: str
    # Upper-cased base type for display.
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
    columns: list[CatalogColumnInfo] = Field(default_factory=list)
    properties: dict[str, str] | None = None
    comment: str | None = None
    # Iceberg table-format version. None for DuckLake, which has no equivalent.
    format_version: int | None = None
    # Raw Iceberg snapshot summary, the source of the free row-count estimate.
    # None for DuckLake, whose row count comes from ducklake_table_stats instead.
    current_snapshot_summary: dict[str, str] | None = None


class SnapshotInfo(_Info):
    """One snapshot of a table.

    Ids stay ints here (64-bit Iceberg snapshot ids); the API layer stringifies
    them for JS safety.

    ``granularity`` is the honest part. An Iceberg snapshot belongs to one table.
    A DuckLake snapshot is a commit against the whole *catalog*, so what is
    returned for a table is the subset of catalog snapshots that changed it —
    the UI has to be able to say so rather than implying a per-table lineage
    that does not exist.
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
    # Whether engines other than DuckDB can read these tables. The honest
    # trade-off a user makes when choosing a kind.
    external_engine_readable: bool = True
    supported_storage_kinds: tuple[str, ...] = ("object_store", "s3", "adls_gen2")


@dataclass
class WriteContext:
    """What a metadata *write* needs beyond its arguments.

    Polaris writes are a REST call and ignore all of this. A DuckLake write is
    SQL that has to run on an agent, under a user, in a workspace — so the
    caller passes it explicitly rather than a backend reaching into request
    state.
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

        The backend owns *how*: Polaris refuses to delete a non-empty namespace
        so its adapter empties it first, while DuckLake does it in one statement.
        The caller should not have to know which.
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

    Lazily imported per kind, matching ``services/compute/backends.get_backend``:
    it keeps an unused kind's dependencies (and its import cost) out of a process
    that never touches it.
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
    """Capabilities by kind, without needing a catalog row or a client.

    Used by the create flow, which has to describe a kind before a catalog of
    that kind exists.
    """
    from api.models.catalog import KIND_DUCKLAKE, KIND_ICEBERG_POLARIS

    if kind == KIND_ICEBERG_POLARIS:
        from api.services.catalog_backends.polaris import POLARIS_CAPABILITIES

        return POLARIS_CAPABILITIES
    if kind == KIND_DUCKLAKE:
        from api.services.catalog_backends.ducklake import DUCKLAKE_CAPABILITIES

        return DUCKLAKE_CAPABILITIES
    raise CatalogBackendUnavailable(f"Unsupported catalog kind: {kind!r}")
