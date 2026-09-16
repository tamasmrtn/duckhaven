"""The Iceberg + Polaris catalog backend.

An adapter, not a rewrite: ``services/polaris.py`` — a 720-line, well-tested,
single-purpose REST client — is untouched, and this wraps it so the router can
ask the same questions of either catalog kind. The Iceberg-specific type mapping
moved here from ``routers/schemas.py``, where it no longer belongs now that not
every catalog is Iceberg.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from api.services.catalog_backends import (
    CatalogBackendBadRequest,
    CatalogBackendConflict,
    CatalogBackendError,
    CatalogBackendNotFound,
    CatalogBackendUnavailable,
    CatalogCapabilities,
    CatalogSchemaInfo,
    CatalogTableInfo,
    SnapshotInfo,
    WriteContext,
)
from api.services.polaris import (
    PolarisBadRequestError,
    PolarisClient,
    PolarisConflictError,
    PolarisError,
    PolarisNotFoundError,
)
from api.services.workspace import ensure_polaris_catalog, polaris_storage

if TYPE_CHECKING:  # pragma: no cover - typing only
    from api.models.catalog import Catalog
    from api.schemas.catalog import ColumnSpec

POLARIS_CAPABILITIES = CatalogCapabilities(
    # An Iceberg snapshot belongs to one table.
    snapshot_granularity="table",
    supports_storage_migration=True,
    # DuckDB's iceberg extension cannot run compaction or snapshot expiry, which
    # is why DuckHaven's maintenance advisor recommends rather than applies.
    maintenance_executable=False,
    # The reason Iceberg is the default kind: Spark, Trino, Flink and PyIceberg
    # can all read these tables.
    external_engine_readable=True,
    supported_storage_kinds=("object_store", "s3", "adls_gen2"),
)

# The small set of allowed scalar types, as Iceberg primitive type strings.
_TYPE_TO_ICEBERG: dict[str, str] = {
    "INTEGER": "int",
    "BIGINT": "long",
    "DOUBLE": "double",
    "VARCHAR": "string",
    "BOOLEAN": "boolean",
    "DATE": "date",
    "TIMESTAMP": "timestamp",
    "DECIMAL": "decimal(38,9)",
}


def column_for_iceberg(spec: ColumnSpec, field_id: int) -> dict[str, object]:
    """Build an Iceberg schema field. Field ids are 1-based and unique."""
    return {
        "id": field_id,
        "name": spec.name,
        "required": not spec.nullable,
        "type": _TYPE_TO_ICEBERG[spec.type],
    }


def _translate(exc: PolarisError) -> CatalogBackendError:
    """Map a Polaris failure onto the seam's vocabulary.

    The mapping is 1:1 with the app-level handler's status codes, so a Polaris
    error surfaces exactly the HTTP response it did before the seam existed.
    """
    if isinstance(exc, PolarisNotFoundError):
        return CatalogBackendNotFound(str(exc))
    if isinstance(exc, PolarisBadRequestError):
        return CatalogBackendBadRequest(str(exc))
    if isinstance(exc, PolarisConflictError):
        return CatalogBackendConflict(str(exc))
    return CatalogBackendUnavailable(str(exc))


class PolarisCatalogBackend:
    """Catalog metadata served by an Apache Polaris Iceberg REST catalog."""

    kind = "iceberg_polaris"

    def __init__(self, polaris: PolarisClient) -> None:
        self._polaris = polaris

    def capabilities(self) -> CatalogCapabilities:
        return POLARIS_CAPABILITIES

    async def ensure(self, catalog: Catalog) -> None:
        """Create the Polaris catalog + default namespace if absent.

        Called on browse as a self-heal, which is also what reconciles a drifted
        bundled-store endpoint onto an existing catalog.
        """
        backend = catalog.storage_backend
        if backend is None:
            raise CatalogBackendError("Catalog points to a missing storage backend")
        storage_type, base_location, extra_storage = polaris_storage(
            backend.kind, backend.root_uri, backend.config
        )
        await ensure_polaris_catalog(
            self._polaris,
            catalog.polaris_name,
            storage_type=storage_type,
            base_location=base_location,
            extra_storage=extra_storage,
        )

    async def provision(self, catalog: Catalog) -> None:
        await self.ensure(catalog)

    async def deprovision(self, catalog: Catalog) -> None:
        """Purge the catalog's namespaces and tables, then the catalog itself.

        Polaris refuses to delete a catalog that still holds namespaces or custom
        catalog roles, so the order matters. NotFound is swallowed so a
        partially-provisioned catalog still drops cleanly.
        """
        try:
            for schema in await self._polaris.list_schemas(catalog.polaris_name):
                for table in await self._polaris.list_tables(catalog.polaris_name, schema.name):
                    await self._polaris.delete_table(
                        catalog.polaris_name, schema.name, table.name, purge=True
                    )
                await self._polaris.delete_schema(catalog.polaris_name, schema.name)
            await self._polaris.delete_catalog_access(catalog.polaris_name)
            await self._polaris.delete_catalog(catalog.polaris_name)
        except PolarisNotFoundError:
            pass
        except PolarisError as exc:
            raise _translate(exc) from exc

    async def list_schemas(self, catalog: Catalog) -> list[CatalogSchemaInfo]:
        try:
            schemas = await self._polaris.list_schemas(catalog.polaris_name)
        except PolarisError as exc:
            raise _translate(exc) from exc
        return [CatalogSchemaInfo.model_validate(s.model_dump()) for s in schemas]

    async def create_schema(
        self, catalog: Catalog, name: str, ctx: WriteContext
    ) -> CatalogSchemaInfo:
        try:
            created = await self._polaris.create_schema(catalog.polaris_name, name)
        except PolarisError as exc:
            raise _translate(exc) from exc
        return CatalogSchemaInfo.model_validate(created.model_dump())

    async def delete_schema(
        self, catalog: Catalog, name: str, ctx: WriteContext, *, cascade: bool = False
    ) -> None:
        try:
            if cascade:
                # Polaris refuses to delete a namespace that still holds tables,
                # so empty it first. Drop-with-purge, so the files go too.
                for table in await self._polaris.list_tables(catalog.polaris_name, name):
                    await self._polaris.delete_table(
                        catalog.polaris_name, name, table.name, purge=True
                    )
            await self._polaris.delete_schema(catalog.polaris_name, name)
        except PolarisError as exc:
            raise _translate(exc) from exc

    async def list_tables(self, catalog: Catalog, schema: str) -> list[CatalogTableInfo]:
        try:
            tables = await self._polaris.list_tables(catalog.polaris_name, schema)
        except PolarisError as exc:
            raise _translate(exc) from exc
        return [CatalogTableInfo.model_validate(t.model_dump()) for t in tables]

    async def get_table(self, catalog: Catalog, schema: str, name: str) -> CatalogTableInfo:
        try:
            table = await self._polaris.get_table(catalog.polaris_name, schema, name)
        except PolarisError as exc:
            raise _translate(exc) from exc
        return CatalogTableInfo.model_validate(table.model_dump())

    async def create_table(
        self,
        catalog: Catalog,
        schema: str,
        name: str,
        columns: list[ColumnSpec],
        ctx: WriteContext,
    ) -> CatalogTableInfo:
        iceberg_columns: list[Any] = [
            column_for_iceberg(spec, idx + 1) for idx, spec in enumerate(columns)
        ]
        try:
            table = await self._polaris.create_table(
                catalog=catalog.polaris_name,
                schema=schema,
                name=name,
                columns=iceberg_columns,
            )
        except PolarisError as exc:
            raise _translate(exc) from exc
        return CatalogTableInfo.model_validate(table.model_dump())

    async def delete_table(
        self, catalog: Catalog, schema: str, name: str, ctx: WriteContext
    ) -> None:
        try:
            # Drop-with-purge, so DROP TABLE reclaims the data files.
            await self._polaris.delete_table(catalog.polaris_name, schema, name, purge=True)
        except PolarisError as exc:
            raise _translate(exc) from exc

    async def list_snapshots(self, catalog: Catalog, schema: str, name: str) -> list[SnapshotInfo]:
        try:
            snapshots = await self._polaris.list_snapshots(catalog.polaris_name, schema, name)
        except PolarisError as exc:
            raise _translate(exc) from exc
        return [SnapshotInfo.model_validate(s.model_dump()) for s in snapshots]
