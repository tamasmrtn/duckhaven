"""The DuckLake catalog backend.

A DuckLake catalog's metadata is a set of ``ducklake_*`` tables in one schema of
the ``ducklake`` database, defined by the DuckLake specification (v1.0). That
makes this backend asymmetric in a way the Polaris one is not:

**Reads go straight to Postgres.** Listing schemas, tables, columns and snapshots
is a ``SELECT`` over the spec's own tables. No REST round-trip, no agent, and no
DuckDB — so I1 holds and browsing a catalog works even with no agent connected.

**Writes go through an agent.** Catalog metadata is only safely mutated through
DuckLake's transaction protocol, which lives in the ``ducklake`` DuckDB
extension. Writing those rows by hand would corrupt the format. So DDL is
generated as SQL and dispatched, which is also why ``WriteContext`` exists.

Does reading it here violate I3 ("Polaris owns catalog metadata; Postgres owns
DuckHaven entities; never persist catalog structure into Postgres")? No — both
halves still hold. DuckHaven's own ORM persists nothing here, and this is not a
cache but the authoritative store, read directly, in a database Alembic does not
manage. That is exactly the relationship Postgres already has with the Polaris
metastore, which has lived in the same instance since day one.

Versioning note: every metadata table is versioned by a half-open
``[begin_snapshot, end_snapshot)`` range, so "currently exists" is
``end_snapshot IS NULL`` and every query below carries that filter.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from api.config import settings
from api.services.catalog_backends import (
    CatalogBackendConflict,
    CatalogBackendError,
    CatalogBackendNotFound,
    CatalogBackendUnavailable,
    CatalogCapabilities,
    CatalogColumnInfo,
    CatalogSchemaInfo,
    CatalogTableInfo,
    SnapshotInfo,
    WriteContext,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from api.models.catalog import Catalog
    from api.schemas.catalog import ColumnSpec

logger = logging.getLogger(__name__)

DUCKLAKE_CAPABILITIES = CatalogCapabilities(
    # The honest one. A DuckLake snapshot is a commit against the whole catalog,
    # not one table, so a table's "history" is the subset of catalog snapshots
    # that touched it. The UI has to be able to say so.
    # Deliberately out of scope: DuckLake's relative paths make migration a copy
    # plus one data_path update rather than Iceberg's metadata-tree rewrite, so
    # the existing engine does not apply and building a second one is premature.
    supports_storage_migration=False,
    # The real product win. Unlike the iceberg extension, DuckDB's ducklake
    # extension can run compaction, snapshot expiry and orphan cleanup.
    # The real product cost, and the reason this is not the default kind: no
    # other engine can open a DuckLake table today.
    external_engine_readable=False,
    supported_storage_kinds=("object_store", "s3", "adls_gen2"),
)

# DuckLake's own type spelling, which is what ducklake_column stores. Mapped to
# DuckHaven's display vocabulary so a DuckLake table's columns read the same as
# an Iceberg table's in the UI. Unknown types pass through unchanged rather than
# being hidden behind a placeholder.
_DUCKLAKE_TYPE_DISPLAY: dict[str, str] = {
    "int8": "TINYINT",
    "int16": "SMALLINT",
    "int32": "INTEGER",
    "int64": "BIGINT",
    "uint8": "UTINYINT",
    "uint16": "USMALLINT",
    "uint32": "UINTEGER",
    "uint64": "UBIGINT",
    "float32": "FLOAT",
    "float64": "DOUBLE",
    "varchar": "VARCHAR",
    "boolean": "BOOLEAN",
    "date": "DATE",
    "blob": "BLOB",
}

# DuckHaven's allowed column types as DuckLake spells them, for CREATE TABLE.
_TYPE_TO_DUCKDB: dict[str, str] = {
    "INTEGER": "INTEGER",
    "BIGINT": "BIGINT",
    "DOUBLE": "DOUBLE",
    "VARCHAR": "VARCHAR",
    "BOOLEAN": "BOOLEAN",
    "DATE": "DATE",
    "TIMESTAMP": "TIMESTAMP",
    "DECIMAL": "DECIMAL(38,9)",
}

_engine: AsyncEngine | None = None


def get_engine() -> AsyncEngine:
    """The owner-credential engine used for metadata reads and schema DDL.

    Module-level and lazily built, rather than created in the app lifespan and
    threaded through every call site, because `backend_for` is reached from
    routers that have no handle on app state. The lifespan calls
    `dispose_engine` on shutdown.
    """
    global _engine
    if _engine is None:
        _engine = create_async_engine(settings.ducklake_database_url, pool_pre_ping=True)
    return _engine


async def dispose_engine() -> None:
    """Close the pool. Called from the app lifespan and by tests."""
    global _engine
    if _engine is not None:
        await _engine.dispose()
        _engine = None


def metadata_schema_for(slug: str) -> str:
    """The Postgres schema name a catalog's ducklake_* tables live in.

    Derived from the slug, which `validate_catalog_slug` has already constrained
    to ``^[a-z][a-z0-9_]*$`` — so the result is injection-safe by construction
    and needs no quoting beyond the identifier quotes used below. Stored on the
    catalog row rather than re-derived at use, so a renamed catalog keeps
    pointing at its physical schema.
    """
    name = f"cat_{slug}"
    if len(name) > 63:  # Postgres identifier limit; a longer name is truncated.
        raise CatalogBackendError(
            f"Catalog name {slug!r} is too long: its metadata schema would exceed "
            "Postgres's 63-character identifier limit."
        )
    return name


def _quote(identifier: str) -> str:
    """Quote a SQL identifier, doubling any embedded quote."""
    return '"' + identifier.replace('"', '""') + '"'


class DuckLakeCatalogBackend:
    """Catalog metadata served by a DuckLake catalog in Postgres."""

    kind = "ducklake"

    def capabilities(self) -> CatalogCapabilities:
        return DUCKLAKE_CAPABILITIES

    # --- Lifecycle ----------------------------------------------------------

    async def ensure(self, catalog: Catalog) -> None:
        """Create the metadata schema and grant the agent role on it.

        Idempotent and called on browse, mirroring how the Polaris backend
        self-heals a missing catalog. The ``ducklake_*`` tables inside the schema
        are *not* created here: they are the DuckLake specification's own
        structure, created by the extension on the first agent ATTACH. Hand-
        writing 28 table definitions would duplicate the spec and break on the
        next spec bump.
        """
        if not settings.ducklake_enabled:
            raise CatalogBackendUnavailable(
                "DuckLake support is disabled. Set DUCKLAKE_ENABLED=true to enable it."
            )
        schema = catalog.metadata_schema
        if not schema:
            raise CatalogBackendError("DuckLake catalog has no metadata schema recorded")

        agent_role = settings.ducklake_agent_user
        try:
            async with get_engine().begin() as conn:
                await conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {_quote(schema)}"))
                # The agent role needs CREATE so the ducklake extension can build
                # its tables on first attach, and DML on them thereafter — that
                # is how DuckLake commits. It is scoped to this one schema.
                await conn.execute(
                    text(f"GRANT USAGE, CREATE ON SCHEMA {_quote(schema)} TO {_quote(agent_role)}")
                )
                await conn.execute(
                    text(
                        f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA "
                        f"{_quote(schema)} TO {_quote(agent_role)}"
                    )
                )
                # Tables the extension creates later are covered by this, so the
                # grant does not have to be re-run after the first attach.
                await conn.execute(
                    text(
                        f"ALTER DEFAULT PRIVILEGES IN SCHEMA {_quote(schema)} "
                        f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {_quote(agent_role)}"
                    )
                )
                await conn.execute(
                    text(
                        f"ALTER DEFAULT PRIVILEGES IN SCHEMA {_quote(schema)} "
                        f"GRANT USAGE, SELECT ON SEQUENCES TO {_quote(agent_role)}"
                    )
                )
        except CatalogBackendError:
            raise
        except Exception as exc:  # noqa: BLE001 - any driver failure is upstream
            raise CatalogBackendUnavailable(f"Could not reach the DuckLake catalog: {exc}") from exc

    async def provision(self, catalog: Catalog) -> None:
        await self.ensure(catalog)

    async def deprovision(self, catalog: Catalog) -> None:
        """Drop the catalog: reclaim its data files, then its metadata schema.

        This is the drop-with-purge the Polaris backend gets from Polaris. It is
        done by deleting the catalog's object-storage prefix rather than by
        running ``ducklake_expire_snapshots`` + ``ducklake_cleanup_old_files`` on
        an agent, for a reason worth stating: ``drop_catalog`` refuses while the
        catalog is attached to any workspace, so by the time this runs there is
        no workspace to dispatch against and no way to ATTACH it. The prefix is
        owned outright by this catalog (``ducklake_data_path`` scopes it per
        slug), so deleting it is both safe and complete.

        Data first, then metadata: the prefix is derived from the catalog row,
        not from the metadata tables, but dropping the schema first would leave
        nothing to retry against if the delete fails halfway.
        """
        if catalog.storage_backend is not None:
            await self._purge_data(catalog)

        schema = catalog.metadata_schema
        if not schema:
            return
        try:
            async with get_engine().begin() as conn:
                await conn.execute(text(f"DROP SCHEMA IF EXISTS {_quote(schema)} CASCADE"))
        except Exception as exc:  # noqa: BLE001
            raise CatalogBackendUnavailable(
                f"Could not drop the DuckLake metadata schema: {exc}"
            ) from exc

    async def _purge_data(self, catalog: Catalog) -> None:
        """Delete everything under the catalog's data path.

        Best-effort: a storage failure must not leave the catalog half-dropped
        and un-droppable. It is logged loudly instead, because the consequence is
        orphaned objects an operator has to clean up, not lost data.
        """
        from api.services.session_credentials import build_storage_block, ducklake_data_path

        try:
            data_path = ducklake_data_path(catalog)
            block = await asyncio.to_thread(build_storage_block, catalog.storage_backend, data_path)
            purge = _purge_azure_prefix if block.get("type") == "azure" else _purge_s3_prefix
            # Paginated deletes over a large prefix, on a thread: inline they
            # would stall the whole API process for the length of the drop.
            await asyncio.to_thread(purge, block, data_path)
        except Exception as exc:  # noqa: BLE001 - never block the drop
            logger.warning(
                "Could not purge data for DuckLake catalog %s; objects may be orphaned "
                "under its prefix and need manual cleanup: %s",
                catalog.slug,
                exc,
            )

    # --- Metadata reads -----------------------------------------------------

    async def _rows(self, catalog: Catalog, sql: str, params: dict[str, Any]) -> list[Any]:
        """Run one read against this catalog's metadata schema.

        A missing table means the catalog exists but has never been attached, so
        the extension has not built its structure yet. That is a legitimate
        state (a freshly created catalog) and reads answer "empty", not "error".
        """
        schema = catalog.metadata_schema
        if not schema:
            raise CatalogBackendError("DuckLake catalog has no metadata schema recorded")
        try:
            async with get_engine().connect() as conn:
                result = await conn.execute(text(sql.format(schema=_quote(schema))), params)
                return list(result.all())
        except Exception as exc:  # noqa: BLE001
            if _is_missing_relation(exc):
                logger.info(
                    "DuckLake catalog %s has no metadata tables yet (never attached)",
                    catalog.slug,
                )
                return []
            raise CatalogBackendUnavailable(f"Could not read DuckLake metadata: {exc}") from exc

    async def list_schemas(self, catalog: Catalog) -> list[CatalogSchemaInfo]:
        rows = await self._rows(
            catalog,
            "SELECT schema_name FROM {schema}.ducklake_schema "
            "WHERE end_snapshot IS NULL ORDER BY schema_name",
            {},
        )
        return [CatalogSchemaInfo(name=r[0], catalog_name=catalog.slug) for r in rows]

    async def list_tables(self, catalog: Catalog, schema: str) -> list[CatalogTableInfo]:
        rows = await self._rows(
            catalog,
            "SELECT t.table_name, t.table_uuid, s.record_count, s.file_size_bytes "
            "FROM {schema}.ducklake_table t "
            "JOIN {schema}.ducklake_schema sc ON sc.schema_id = t.schema_id "
            "LEFT JOIN {schema}.ducklake_table_stats s ON s.table_id = t.table_id "
            "WHERE t.end_snapshot IS NULL AND sc.end_snapshot IS NULL "
            "AND sc.schema_name = :schema ORDER BY t.table_name",
            {"schema": schema},
        )
        return [
            _table_info(catalog, schema, name=r[0], table_uuid=r[1], record_count=r[2])
            for r in rows
        ]

    async def get_table(self, catalog: Catalog, schema: str, name: str) -> CatalogTableInfo:
        rows = await self._rows(
            catalog,
            "SELECT t.table_id, t.table_uuid, st.record_count, st.file_size_bytes "
            "FROM {schema}.ducklake_table t "
            "JOIN {schema}.ducklake_schema sc ON sc.schema_id = t.schema_id "
            "LEFT JOIN {schema}.ducklake_table_stats st ON st.table_id = t.table_id "
            "WHERE t.end_snapshot IS NULL AND sc.end_snapshot IS NULL "
            "AND sc.schema_name = :schema AND t.table_name = :name",
            {"schema": schema, "name": name},
        )
        if not rows:
            raise CatalogBackendNotFound(f"Table {schema}.{name} does not exist")
        table_id, table_uuid, record_count, _size = rows[0]

        column_rows = await self._rows(
            catalog,
            "SELECT column_name, column_type, nulls_allowed, column_order "
            "FROM {schema}.ducklake_column "
            "WHERE table_id = :table_id AND end_snapshot IS NULL AND parent_column IS NULL "
            "ORDER BY column_order",
            {"table_id": table_id},
        )
        columns = [
            CatalogColumnInfo(
                name=c[0],
                type_text=str(c[1]),
                type_name=_DUCKLAKE_TYPE_DISPLAY.get(str(c[1]).lower(), str(c[1]).upper()),
                position=int(c[3] or idx),
                nullable=bool(c[2]),
            )
            for idx, c in enumerate(column_rows)
        ]
        return _table_info(
            catalog,
            schema,
            name=name,
            table_uuid=table_uuid,
            record_count=record_count,
            columns=columns,
        )

    async def list_snapshots(self, catalog: Catalog, schema: str, name: str) -> list[SnapshotInfo]:
        """The catalog snapshots in which this table changed.

        DuckLake snapshots are catalog-wide, so this is a derivation rather than
        a lookup, and the returned SnapshotInfo is marked
        ``granularity="catalog"`` so the UI can say what these actually are.

        It unions three sources, each of which alone misses real history
        (verified against DuckLake 1.0):

        - ``ducklake_table``'s own begin/end snapshot — without it a table
          created empty has no history at all, and a drop is invisible;
        - ``ducklake_data_file`` — inserts and compaction;
        - ``ducklake_delete_file`` — deletes that wrote a delete file.

        ``ducklake_snapshot_changes`` would be the direct answer, but its
        ``changes_made`` column is free text
        (``created_table:"analytics"."t",inserted_into_table:2``) that would have
        to be parsed; these three are typed columns.

        Known gap: a change small enough to be *inlined* into the catalog
        database (10 rows by default) writes no file row, so a delete of a
        handful of rows can leave no trace here. Those snapshots still exist and
        are still queryable by version; they just do not appear in this list.
        """
        rows = await self._rows(
            catalog,
            "WITH tbl AS ("
            "  SELECT t.table_id, t.begin_snapshot, t.end_snapshot "
            "  FROM {schema}.ducklake_table t "
            "  JOIN {schema}.ducklake_schema sc ON sc.schema_id = t.schema_id "
            "  WHERE sc.schema_name = :schema AND t.table_name = :name"
            "), touched AS ("
            "  SELECT begin_snapshot AS sid FROM tbl "
            "  UNION SELECT end_snapshot FROM tbl "
            "  UNION SELECT f.begin_snapshot FROM {schema}.ducklake_data_file f "
            "    JOIN tbl ON tbl.table_id = f.table_id "
            "  UNION SELECT f.end_snapshot FROM {schema}.ducklake_data_file f "
            "    JOIN tbl ON tbl.table_id = f.table_id "
            "  UNION SELECT d.begin_snapshot FROM {schema}.ducklake_delete_file d "
            "    JOIN tbl ON tbl.table_id = d.table_id "
            "  UNION SELECT d.end_snapshot FROM {schema}.ducklake_delete_file d "
            "    JOIN tbl ON tbl.table_id = d.table_id"
            ") "
            "SELECT s.snapshot_id, s.snapshot_time, s.schema_version "
            "FROM {schema}.ducklake_snapshot s "
            "JOIN touched ON touched.sid = s.snapshot_id "
            "ORDER BY s.snapshot_id DESC",
            {"schema": schema, "name": name},
        )
        if not rows:
            return []
        newest = rows[0][0]
        return [
            SnapshotInfo(
                snapshot_id=int(r[0]),
                timestamp_ms=_epoch_ms(r[1]),
                schema_id=int(r[2]) if r[2] is not None else None,
                is_current=r[0] == newest,
                granularity="catalog",
            )
            for r in rows
        ]

    # --- Metadata writes (dispatched to an agent) ---------------------------
    #
    # DuckLake commits through its DuckDB extension's transaction protocol.
    # There is no REST endpoint to call and no safe way to write the catalog
    # tables directly, so DDL is generated here and executed on an agent through
    # the same fabric the maintenance scanner uses for health probes. Rows are
    # tagged origin="metadata" so they stay out of the user's query history.

    async def _run_ddl(self, catalog: Catalog, sql: str, ctx: WriteContext, *, what: str) -> None:
        from api.services import query as query_service

        agent = await query_service.pick_agent_for(ctx.db, ctx.workspace, principal_id=ctx.user.id)
        if agent is None:
            raise CatalogBackendUnavailable(
                f"No compatible agent is connected to {what}. A DuckLake catalog's DDL "
                "runs on an agent, because only the DuckLake extension can commit it."
            )
        query = await query_service.run_sync_query(
            ctx.db,
            workspace=ctx.workspace,
            agent=agent,
            user_id=ctx.user.id,
            sql=sql,
            origin="metadata",
            active_catalog=catalog.slug,
            timeout_s=60.0,
        )
        if query.status != "done":
            detail = query.error or f"statement {query.status}"
            if "already exists" in detail.lower():
                raise CatalogBackendConflict(detail)
            if "does not exist" in detail.lower() or "not found" in detail.lower():
                raise CatalogBackendNotFound(detail)
            raise CatalogBackendUnavailable(f"Could not {what}: {detail}")

    async def create_schema(
        self, catalog: Catalog, name: str, ctx: WriteContext
    ) -> CatalogSchemaInfo:
        await self._run_ddl(
            catalog,
            f"CREATE SCHEMA {_quote(catalog.slug)}.{_quote(name)}",
            ctx,
            what=f"create schema {name!r}",
        )
        return CatalogSchemaInfo(name=name, catalog_name=catalog.slug)

    async def delete_schema(
        self, catalog: Catalog, name: str, ctx: WriteContext, *, cascade: bool = False
    ) -> None:
        """Drop the schema, optionally with its tables in the same statement.

        ``cascade`` matters here in a way it does not for Polaris: each DuckLake
        DDL is a round-trip to an agent, so dropping a 50-table schema table by
        table is 50 sequential dispatches holding one HTTP request open. DuckDB
        does it in one.
        """
        suffix = " CASCADE" if cascade else ""
        await self._run_ddl(
            catalog,
            f"DROP SCHEMA {_quote(catalog.slug)}.{_quote(name)}{suffix}",
            ctx,
            what=f"drop schema {name!r}",
        )

    async def create_table(
        self,
        catalog: Catalog,
        schema: str,
        name: str,
        columns: list[ColumnSpec],
        ctx: WriteContext,
    ) -> CatalogTableInfo:
        # No type check here: ColumnSpec.type is AllowedColumnType, a Literal of
        # eight scalars, every one of which DuckLake can represent. The types it
        # cannot (ARRAY, ENUM, UNION, …) are unreachable through this API.
        defs = ", ".join(
            f"{_quote(c.name)} {_TYPE_TO_DUCKDB[c.type]}" + ("" if c.nullable else " NOT NULL")
            for c in columns
        )
        await self._run_ddl(
            catalog,
            f"CREATE TABLE {_quote(catalog.slug)}.{_quote(schema)}.{_quote(name)} ({defs})",
            ctx,
            what=f"create table {schema}.{name}",
        )
        # Read the created table back rather than synthesising it, so the caller
        # gets the catalog's own view (table_uuid especially, which the metadata
        # sidecar records as this table's identity at birth).
        return await self.get_table(catalog, schema, name)

    async def delete_table(
        self, catalog: Catalog, schema: str, name: str, ctx: WriteContext
    ) -> None:
        """Drop the table. Its Parquet is NOT reclaimed here.

        The Polaris sibling passes ``purge=True`` and gets its files back
        immediately. DuckLake keeps the files so the table stays reachable
        through time travel, and reclaims them only when the snapshots that
        reference it are expired and `ducklake_cleanup_old_files` runs — which
        the maintenance advisor recommends and DuckHaven does not yet execute.
        Until then the objects remain under the catalog's prefix, and dropping
        the whole catalog purges them.
        """
        await self._run_ddl(
            catalog,
            f"DROP TABLE {_quote(catalog.slug)}.{_quote(schema)}.{_quote(name)}",
            ctx,
            what=f"drop table {schema}.{name}",
        )


def _table_info(
    catalog: Catalog,
    schema: str,
    *,
    name: str,
    table_uuid: Any,
    record_count: Any,
    columns: list[CatalogColumnInfo] | None = None,
) -> CatalogTableInfo:
    return CatalogTableInfo(
        name=name,
        catalog_name=catalog.slug,
        schema_name=schema,
        table_id=str(table_uuid) if table_uuid is not None else None,
        table_type="MANAGED",
        data_source_format="DUCKLAKE",
        storage_location=None,
        columns=columns or [],
        properties={},
        # Iceberg concepts with no DuckLake equivalent. Left None rather than
        # faked, so the UI can omit the row instead of showing a wrong number.
        format_version=None,
        current_snapshot_summary=(
            # Surfaced through the same field the Iceberg path uses for its free
            # row-count estimate, because it has the same status: the spec says
            # ducklake_table_stats.record_count "can be approximate", and it is —
            # measured on DuckLake 1.0, it still read 5000 after 10 rows were
            # deleted from a 5000-row table. It is an estimate available without
            # a scan, not a true count; a true count comes from the same
            # `recount` probe the Iceberg path uses.
            {"total-records": str(int(record_count))} if record_count is not None else None
        ),
    )


# 42P01 undefined_table, 3F000 invalid_schema_name.
_MISSING_RELATION_SQLSTATES = frozenset({"42P01", "3F000"})


def _epoch_ms(value: datetime) -> int:
    """Epoch milliseconds from a snapshot timestamp.

    ``ducklake_snapshot.snapshot_time`` is TIMESTAMPTZ per the spec, but a
    driver or a hand-made schema can still hand back a naive datetime, and
    ``.timestamp()`` would then read it as *local* time and shift every snapshot
    by the server's UTC offset. Assume UTC when the tzinfo is missing.
    """
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return int(value.timestamp() * 1000)


def _is_missing_relation(exc: Exception) -> bool:
    """True when the failure is "that table/schema does not exist".

    Reads the driver's SQLSTATE off the wrapped exception rather than matching
    its ``repr``: the repr is not part of asyncpg's contract, and if it changed
    this would silently turn into a 502 on every browse.
    """
    for candidate in (getattr(exc, "orig", None), exc):
        sqlstate = getattr(candidate, "sqlstate", None)
        if sqlstate in _MISSING_RELATION_SQLSTATES:
            return True
    return False


def _purge_s3_prefix(block: dict[str, Any], data_path: str) -> None:
    """Delete every object under an s3:// prefix, in batches of 1000."""
    import boto3
    from botocore.config import Config

    parsed = urlparse(data_path)
    bucket, prefix = parsed.netloc, parsed.path.lstrip("/")
    endpoint = str(block.get("endpoint") or "")
    if endpoint and "://" not in endpoint:
        endpoint = f"{'https' if block.get('use_ssl') else 'http'}://{endpoint}"
    client = boto3.client(
        "s3",
        endpoint_url=endpoint or None,
        region_name=str(block.get("region") or "") or None,
        aws_access_key_id=str(block.get("key_id") or "") or None,
        aws_secret_access_key=str(block.get("secret") or "") or None,
        aws_session_token=str(block.get("session_token") or "") or None,
        config=Config(
            s3={"addressing_style": "path" if block.get("url_style") == "path" else "auto"}
        ),
    )
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        keys = [{"Key": obj["Key"]} for obj in page.get("Contents", [])]
        if keys:
            client.delete_objects(Bucket=bucket, Delete={"Objects": keys})


def _purge_azure_prefix(block: dict[str, Any], data_path: str) -> None:
    """Delete every blob under an abfss:// prefix."""
    from azure.storage.blob import BlobServiceClient

    parsed = urlparse(data_path)
    container = parsed.username or parsed.netloc.split("@")[0]
    prefix = parsed.path.lstrip("/")
    service = BlobServiceClient.from_connection_string(str(block["connection_string"]))
    client = service.get_container_client(container)
    for blob in client.list_blobs(name_starts_with=prefix):
        client.delete_blob(blob.name)
