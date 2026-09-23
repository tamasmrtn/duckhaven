"""The DuckLake catalog backend.

Metadata lives in ``ducklake_*`` tables in one schema of the ``ducklake``
database. Reads go straight to Postgres, so browsing works with no compute
attached. Writes are dispatched to an agent, because only the DuckLake
extension can safely commit them. This is the authoritative store, not a
cache, so it does not violate I3.

Rows are versioned by ``[begin_snapshot, end_snapshot)``; "currently exists"
is ``end_snapshot IS NULL``.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from api.config import settings
from api.db.entra import attach_entra_auth
from api.db.session import engine_kwargs
from api.metrics import record_ducklake_query
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
    # File paths are relative to the catalog's data path, so relocating is a
    # prefix copy plus one row.
    supports_storage_migration=True,
    external_engine_readable=False,
    supports_maintenance_apply=True,
    supports_iceberg_export=True,
    supports_agentless_ddl=False,
    supported_storage_kinds=("object_store", "s3", "adls_gen2"),
)

# ducklake_column type spelling -> DuckHaven display type. Unknown types pass through.
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
    """The owner-credential engine for metadata reads and schema DDL.

    Module-level because `backend_for` is called from routers with no app
    state. Configured like the control-plane engine, Entra auth included.
    """
    global _engine
    if _engine is None:
        url = settings.ducklake_database_url
        _engine = create_async_engine(url, **engine_kwargs(url))
        if settings.db_auth_mode == "entra":
            attach_entra_auth(_engine)
    return _engine


async def ping() -> None:
    """Readiness check for the catalog database, which has its own pool and role."""
    async with get_engine().connect() as conn:
        await conn.execute(text("SELECT 1"))


async def dispose_engine() -> None:
    """Close the pool. Called from the app lifespan and by tests."""
    global _engine
    if _engine is not None:
        await _engine.dispose()
        _engine = None


def ducklake_identifiers(slug: str) -> tuple[str, str]:
    """The Postgres schema and login role for a catalog.

    Injection-safe: `validate_catalog_slug` constrains the slug to
    ``^[a-z][a-z0-9_]*$``.
    """
    schema, role = f"cat_{slug}", f"dl_{slug}"
    too_long = max(schema, role, key=len)
    if len(too_long) > 63:  # Postgres truncates longer identifiers.
        raise CatalogBackendError(
            f"Catalog name {slug!r} is too long: its metadata schema and role would "
            "exceed Postgres's 63-character identifier limit."
        )
    return schema, role


def metadata_schema_for(slug: str) -> str:
    """The Postgres schema a catalog's ducklake_* tables live in."""
    return ducklake_identifiers(slug)[0]


def agent_role_for(slug: str) -> str:
    """The PostgreSQL login this catalog's agents authenticate with."""
    return ducklake_identifiers(slug)[1]


def ducklake_role_password(catalog: Catalog) -> str:
    """This catalog's PostgreSQL password, from its own credential row.

    Deliberately no fallback to a deployment-wide password: that would silently
    void per-catalog isolation for older catalogs.
    """
    # During creation the row cannot exist yet, so the password rides on the object.
    if pending := getattr(catalog, "pending_ducklake_password", None):
        return pending
    credential = catalog.ducklake_credential
    if credential is None or not credential.token:
        raise CatalogBackendUnavailable(
            f"DuckLake catalog {catalog.slug!r} has no metadata credential. Run "
            "POST /api/admin/catalogs/ducklake/reconcile-roles to create one."
        )
    return credential.token


def new_role_password() -> str:
    """A fresh password for a catalog's PostgreSQL role."""
    return secrets.token_urlsafe(32)


def _quote(identifier: str) -> str:
    """Quote a SQL identifier, doubling any embedded quote."""
    return '"' + identifier.replace('"', '""') + '"'


class DuckLakeCatalogBackend:
    """Catalog metadata served by a DuckLake catalog in Postgres."""

    kind = "ducklake"

    def capabilities(self) -> CatalogCapabilities:
        return DUCKLAKE_CAPABILITIES

    # --- Lifecycle ----------------------------------------------------------

    async def ensure(self, catalog: Catalog, *, grant_dml: bool = True) -> None:
        """Create the metadata schema, role and grants for this catalog.

        Idempotent, called on browse. The ``ducklake_*`` tables are left to the
        extension's first ATTACH so they track its spec version.

        ``grant_dml=False`` leaves write access alone, so a browse during a
        storage migration does not re-grant the writes the freeze revoked.
        """
        if not settings.ducklake_enabled:
            raise CatalogBackendUnavailable(
                "DuckLake support is disabled. Set DUCKLAKE_ENABLED=true to enable it."
            )
        schema = catalog.metadata_schema
        if not schema:
            raise CatalogBackendError("DuckLake catalog has no metadata schema recorded")

        agent_role = agent_role_for(catalog.slug)
        password = ducklake_role_password(catalog)
        try:
            async with get_engine().begin() as conn:
                # One login per catalog, so a bypassed statement gate still
                # cannot reach another catalog. The password is re-applied on
                # every call, which is how rotation takes effect.
                exists = await conn.scalar(
                    text("SELECT 1 FROM pg_roles WHERE rolname = :role").bindparams(role=agent_role)
                )
                # CREATE/ALTER ROLE take no bind parameters, so the password goes
                # through a transaction-local setting and is quoted server-side
                # with format(%L).
                await conn.execute(
                    text("SELECT set_config('duckhaven.role_password', :pw, true)").bindparams(
                        pw=password
                    )
                )
                verb = "CREATE" if not exists else "ALTER"
                await conn.execute(
                    text(
                        f"DO $do$ BEGIN EXECUTE format("
                        f"'{verb} ROLE %I {'LOGIN ' if not exists else 'WITH '}PASSWORD %L', "
                        f"'{agent_role}', current_setting('duckhaven.role_password')); END $do$"
                    )
                )
                # TEMPORARY: the extension's `set_option` uses a temp table, and
                # PUBLIC's implicit grant was revoked on this database.
                await conn.execute(
                    text(
                        f"GRANT CONNECT, TEMPORARY ON DATABASE "
                        f"{_quote(settings.ducklake_agent_database)} TO {_quote(agent_role)}"
                    )
                )
                await conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {_quote(schema)}"))
                # CREATE lets the extension build its tables on first attach.
                await conn.execute(
                    text(f"GRANT USAGE, CREATE ON SCHEMA {_quote(schema)} TO {_quote(agent_role)}")
                )
                write = ", INSERT, UPDATE, DELETE" if grant_dml else ""
                await conn.execute(
                    text(
                        f"GRANT SELECT{write} ON ALL TABLES IN SCHEMA "
                        f"{_quote(schema)} TO {_quote(agent_role)}"
                    )
                )
                # Covers tables the extension creates later.
                await conn.execute(
                    text(
                        f"ALTER DEFAULT PRIVILEGES IN SCHEMA {_quote(schema)} "
                        f"GRANT SELECT{write} ON TABLES TO {_quote(agent_role)}"
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
        """Drop the catalog: purge its data files, then its metadata schema.

        Data is deleted by prefix rather than ``ducklake_cleanup_*`` because the
        catalog is already detached. Data goes first so a failure is retryable.
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
        await self._drop_role(catalog)

    async def _drop_role(self, catalog: Catalog) -> None:
        """Drop the catalog's login role, after its schema is gone.

        Best-effort, like ``_purge_data``: a leftover role must not make the
        catalog undroppable.
        """
        role = agent_role_for(catalog.slug)
        try:
            async with get_engine().begin() as conn:
                exists = await conn.scalar(
                    text("SELECT 1 FROM pg_roles WHERE rolname = :role").bindparams(role=role)
                )
                if not exists:
                    return
                await conn.execute(text(f"REASSIGN OWNED BY {_quote(role)} TO CURRENT_USER"))
                await conn.execute(text(f"DROP OWNED BY {_quote(role)}"))
                await conn.execute(text(f"DROP ROLE IF EXISTS {_quote(role)}"))
        except Exception as exc:  # noqa: BLE001 - never block the drop
            logger.warning(
                "Could not drop the PostgreSQL role for DuckLake catalog %s; it remains "
                "with no schema to reach and needs manual cleanup: %s",
                catalog.slug,
                exc,
            )

    async def _purge_data(self, catalog: Catalog) -> None:
        """Delete everything under the catalog's data path. Best-effort; logs on failure."""
        from api.services.session_credentials import build_storage_block, ducklake_data_path

        try:
            data_path = ducklake_data_path(catalog)
            block = await asyncio.to_thread(build_storage_block, catalog.storage_backend, data_path)
            purge = _purge_azure_prefix if block.get("type") == "azure" else _purge_s3_prefix
            await asyncio.to_thread(purge, block, data_path)
        except Exception as exc:  # noqa: BLE001 - never block the drop
            logger.warning(
                "Could not purge data for DuckLake catalog %s; objects may be orphaned "
                "under its prefix and need manual cleanup: %s",
                catalog.slug,
                exc,
            )

    # --- Metadata reads -----------------------------------------------------

    async def _rows(
        self, catalog: Catalog, sql: str, params: dict[str, Any], *, operation: str = "read"
    ) -> list[Any]:
        """Run one read against this catalog's metadata schema.

        Missing tables mean the catalog was never attached, so reads return
        empty. ``operation`` is the metric label; kept caller-supplied so the
        label set stays bounded.
        """
        schema = catalog.metadata_schema
        if not schema:
            raise CatalogBackendError("DuckLake catalog has no metadata schema recorded")
        started = time.perf_counter()
        try:
            async with get_engine().connect() as conn:
                result = await conn.execute(text(sql.format(schema=_quote(schema))), params)
                rows = list(result.all())
            record_ducklake_query(operation, "ok", time.perf_counter() - started)
            return rows
        except Exception as exc:  # noqa: BLE001
            status = "empty" if _is_missing_relation(exc) else "error"
            record_ducklake_query(operation, status, time.perf_counter() - started)
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
            operation="list_schemas",
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
            operation="list_tables",
        )
        return [
            _table_info(
                catalog,
                schema,
                name=r[0],
                table_uuid=r[1],
                record_count=r[2],
                size_bytes=r[3],
            )
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
            operation="get_table",
        )
        if not rows:
            raise CatalogBackendNotFound(f"Table {schema}.{name} does not exist")
        table_id, table_uuid, record_count, size_bytes = rows[0]

        column_rows = await self._rows(
            catalog,
            "SELECT column_name, column_type, nulls_allowed, column_order "
            "FROM {schema}.ducklake_column "
            "WHERE table_id = :table_id AND end_snapshot IS NULL AND parent_column IS NULL "
            "ORDER BY column_order",
            {"table_id": table_id},
            operation="get_table_columns",
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
            size_bytes=size_bytes,
            columns=columns,
        )

    async def list_snapshots(self, catalog: Catalog, schema: str, name: str) -> list[SnapshotInfo]:
        """The catalog snapshots in which this table changed.

        DuckLake snapshots are catalog-wide, so this unions ``ducklake_table``,
        ``ducklake_data_file``, ``ducklake_delete_file`` and, for inlined writes
        that leave no file row, ``ducklake_snapshot_changes``. Its
        ``inlined_<verb>:<table_id>`` tokens are matched one at a time, since
        ``LIKE '%:1%'`` would also match table 13.
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
            "  UNION SELECT c.snapshot_id FROM {schema}.ducklake_snapshot_changes c, tbl "
            "    WHERE EXISTS ("
            "      SELECT 1 FROM unnest(string_to_array(c.changes_made, ',')) AS tok "
            "      WHERE tok LIKE 'inlined@_%' ESCAPE '@' "
            "        AND split_part(tok, ':', 2) = tbl.table_id::text)"
            ") "
            "SELECT s.snapshot_id, s.snapshot_time, s.schema_version "
            "FROM {schema}.ducklake_snapshot s "
            "JOIN touched ON touched.sid = s.snapshot_id "
            "ORDER BY s.snapshot_id DESC",
            {"schema": schema, "name": name},
            operation="list_snapshots",
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
    # origin="metadata" keeps these out of the user's query history.

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
        """Drop the schema; ``cascade`` saves one agent round-trip per table."""
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
        # Read back for the catalog-assigned table_uuid.
        return await self.get_table(catalog, schema, name)

    async def delete_table(
        self, catalog: Catalog, schema: str, name: str, ctx: WriteContext
    ) -> None:
        """Drop the table. Its Parquet stays for time travel until snapshots
        expire and ``ducklake_cleanup_old_files`` runs.
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
    size_bytes: Any = None,
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
        size_bytes=int(size_bytes) if size_bytes is not None else None,
        columns=columns or [],
        properties={},
        format_version=None,
        current_snapshot_summary=(
            # Approximate per the spec, like Iceberg's estimate.
            {"total-records": str(int(record_count))} if record_count is not None else None
        ),
    )


# 42P01 undefined_table, 3F000 invalid_schema_name.
_MISSING_RELATION_SQLSTATES = frozenset({"42P01", "3F000"})


def _epoch_ms(value: datetime) -> int:
    """Epoch milliseconds from a snapshot timestamp, assuming UTC when naive."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return int(value.timestamp() * 1000)


def _is_missing_relation(exc: Exception) -> bool:
    """True when the failure is "that table/schema does not exist", by SQLSTATE."""
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
