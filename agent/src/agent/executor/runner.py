"""DuckDB query runner.

`run_query_sync` accepts the workspace's backend descriptor, the
workspace slug (used as the Polaris warehouse), and the Polaris
connection info to ATTACH. DuckDB's `iceberg` extension performs the
OAuth2 client-credentials exchange itself and, for cloud backends,
obtains short-lived storage credentials from Polaris via access
delegation — so the runner injects no storage secrets of its own.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb

logger = logging.getLogger(__name__)

# Fixed identifiers for the per-connection iceberg secret and attached catalog.
_ICEBERG_SECRET = "dh_iceberg"
_CATALOG_ALIAS = "dh_catalog"
# Default namespace to `USE`. Must match the API's default (see
# api/services/workspace.DEFAULT_SCHEMA). `USE <catalog>.<schema>` sets both the
# default catalog and schema; a bare `USE <catalog>` does not reliably resolve
# an attached Iceberg REST catalog (lazy namespace loading).
_DEFAULT_NAMESPACE = "analytics"

# Backend kind -> DuckDB storage-IO extension (loaded so DuckDB can read/write
# the object store with the credentials Polaris vends). Every backend is object
# storage: object_store is backed by the bundled MinIO (S3) and needs httpfs.
_BACKEND_IO_EXTENSION: dict[str, str] = {
    "object_store": "httpfs",
    "s3": "httpfs",
    "adls_gen2": "azure",
}
# All backends are object storage, so all get vended credentials from Polaris.
_VENDED_BACKENDS = {"object_store", "s3", "adls_gen2"}


def _is_single_select(sql: str) -> bool:
    """True when the body is exactly one `SELECT` — the only shape we materialize
    to Parquet. Everything else (DDL/DML, multi-statement scripts) is executed
    directly and produces no result file."""
    try:
        statements = duckdb.extract_statements(sql)
    except Exception:  # noqa: BLE001 - a parse failure surfaces when executed
        return False
    return len(statements) == 1 and statements[0].type == duckdb.StatementType.SELECT


def _safe_install_load(conn: duckdb.DuckDBPyConnection, name: str) -> bool:
    """INSTALL + LOAD an extension; log + return False on failure."""
    try:
        conn.execute(f"INSTALL {name}")
        conn.execute(f"LOAD {name}")
        return True
    except Exception as exc:  # noqa: BLE001 - any failure is recoverable
        logger.warning("Failed to load %s: %s", name, exc)
        return False


def _iceberg_metadata(conn: duckdb.DuckDBPyConnection, schema: str, table: str) -> dict[str, Any]:
    """Best-effort Iceberg-native metadata for a table in the attached catalog.

    Returns the current snapshot id + timestamp, the data-file count, and a
    has-deletes flag (true when the table carries position/equality delete files
    — the same probe the future merge-on-read read guard will use). Each field is
    independently best-effort: a probe failure (e.g. an older `iceberg` extension
    lacking a function) degrades that field to None rather than failing the
    query. The catalog must already be ATTACHed.
    """
    ident = f'{_CATALOG_ALIAS}."{schema}"."{table}"'
    meta: dict[str, Any] = {
        "snapshot_id": None,
        "snapshot_at": None,
        "data_file_count": None,
        "has_deletes": None,
    }
    try:
        snap = conn.execute(
            f"SELECT snapshot_id, timestamp_ms FROM iceberg_snapshots({ident}) "
            "ORDER BY sequence_number DESC LIMIT 1"
        ).fetchone()
        if snap:
            meta["snapshot_id"] = snap[0]
            ts = snap[1]
            if isinstance(ts, datetime):
                meta["snapshot_at"] = ts.isoformat()
            elif isinstance(ts, (int, float)):
                meta["snapshot_at"] = datetime.fromtimestamp(ts / 1000, tz=UTC).isoformat()
    except Exception as exc:  # noqa: BLE001 - metadata is best-effort
        logger.warning("iceberg_snapshots failed for %s.%s: %s", schema, table, exc)
    try:
        # The column classifying data vs delete files moved from `content`
        # (DATA/POSITION_DELETES/EQUALITY_DELETES) to `manifest_content`
        # (DATA/DELETE) in newer DuckDB iceberg extensions; `content` now carries
        # the manifest-entry status (ADDED/EXISTING/DELETED). Pick whichever the
        # running extension exposes — `content` still exists in the new schema, so
        # we must inspect the columns rather than just querying it.
        columns = [
            d[0]
            for d in conn.execute(f"SELECT * FROM iceberg_metadata({ident}) LIMIT 0").description
        ]
        classify = "manifest_content" if "manifest_content" in columns else "content"
        rows = conn.execute(
            f"SELECT {classify}, count(*) FROM iceberg_metadata({ident}) GROUP BY {classify}"
        ).fetchall()
        if rows:
            counts = {str(content): n for content, n in rows}
            meta["data_file_count"] = counts.get("DATA", 0)
            meta["has_deletes"] = any(
                key in counts for key in ("DELETE", "POSITION_DELETES", "EQUALITY_DELETES")
            )
    except Exception as exc:  # noqa: BLE001 - metadata is best-effort
        logger.warning("iceberg_metadata failed for %s.%s: %s", schema, table, exc)
    return meta


def _attach_polaris(
    conn: duckdb.DuckDBPyConnection,
    *,
    warehouse: str,
    polaris: dict[str, Any],
    delegation_mode: str,
    default_schema: str,
) -> None:
    """Create the iceberg OAuth2 secret and ATTACH the Polaris catalog.

    DuckDB exchanges the client credentials for a token itself; with
    `vended_credentials` Polaris also vends scoped storage creds on access.
    """
    endpoint = str(polaris["endpoint"]).rstrip("/")
    conn.execute(
        f"CREATE SECRET {_ICEBERG_SECRET} "
        "(TYPE ICEBERG, CLIENT_ID ?, CLIENT_SECRET ?, OAUTH2_SERVER_URI ?)",
        [
            polaris["client_id"],
            polaris["client_secret"],
            f"{endpoint}/api/catalog/v1/oauth/tokens",
        ],
    )
    # ATTACH does not accept bind parameters, so inline the warehouse and
    # endpoint as quoted literals (single quotes escaped). The warehouse is the
    # workspace slug and the endpoint comes from agent config — neither is
    # user-supplied SQL.
    wh = warehouse.replace("'", "''")
    cat_endpoint = f"{endpoint}/api/catalog".replace("'", "''")
    conn.execute(
        f"ATTACH '{wh}' AS {_CATALOG_ALIAS} "
        f"(TYPE ICEBERG, SECRET {_ICEBERG_SECRET}, ENDPOINT '{cat_endpoint}', "
        f"ACCESS_DELEGATION_MODE '{delegation_mode}')"
    )
    # `USE <catalog>.<schema>` sets the default catalog (so the user's
    # schema-qualified SQL resolves) and a default schema (for unqualified
    # names). A bare `USE <catalog>` does not reliably resolve the attached
    # Iceberg catalog's namespaces.
    schema = (default_schema or _DEFAULT_NAMESPACE).replace('"', '""')
    conn.execute(f'USE {_CATALOG_ALIAS}."{schema}"')


def run_query_sync(
    sql: str,
    result_path: Path,
    *,
    memory_bytes: int,
    threads: int,
    backend: dict[str, Any] | None = None,
    workspace_slug: str | None = None,
    polaris: dict[str, Any] | None = None,
    default_schema: str | None = None,
    stats_for: dict[str, str] | None = None,
    on_connect: Callable[[duckdb.DuckDBPyConnection], None] | None = None,
) -> dict[str, Any]:
    """Run a query through DuckDB.

    A single `SELECT` is materialized to Parquet (`wrote_result=True`); any other
    statement — DDL or DML, including multi-statement scripts — is executed
    directly with no result file (`wrote_result=False`).

    Optional kwargs (passed by the control plane):
    - `backend`: workspace storage backend descriptor `{kind, root_uri}`.
    - `workspace_slug`: used as the Polaris warehouse (catalog) name.
    - `polaris`: `{endpoint, client_id, client_secret}`. When set together
      with `workspace_slug`, ATTACH the workspace's Polaris catalog before
      running the user SQL.
    - `on_connect`: called with the freshly-opened connection so the
      supervisor can `interrupt()` it on timeout/cancel (G-D2-a).
    """
    conn = duckdb.connect()
    if on_connect is not None:
        on_connect(conn)
    try:
        # The admission manager sizes each query's slice of the agent's budget
        # (memory_bytes + threads) so concurrent sessions never oversubscribe the
        # cgroup memory limit. DuckDB's default thread count ignores the cgroup
        # CPU quota, so `threads` is set explicitly.
        mem_gb = memory_bytes / 1024**3
        conn.execute(f"SET memory_limit='{mem_gb}GB'")
        conn.execute(f"SET threads={threads}")

        backend_kind = (backend or {}).get("kind")

        # Load the storage-IO extension so DuckDB can read/write the object
        # store with the credentials Polaris vends.
        if (io_ext := _BACKEND_IO_EXTENSION.get(backend_kind or "")) is not None:
            _safe_install_load(conn, io_ext)

        # Attach the workspace's Polaris catalog so the user's SQL can
        # reference tables by name (e.g. SELECT * FROM main.events).
        if workspace_slug and polaris:
            if _safe_install_load(conn, "iceberg"):
                delegation = "vended_credentials" if backend_kind in _VENDED_BACKENDS else "none"
                try:
                    _attach_polaris(
                        conn,
                        warehouse=workspace_slug,
                        polaris=polaris,
                        delegation_mode=delegation,
                        default_schema=default_schema or _DEFAULT_NAMESPACE,
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Polaris ATTACH failed for %s: %s", workspace_slug, exc)

        start = time.monotonic()
        wrote_result = _is_single_select(sql)
        result_bytes: int | None = None
        if wrote_result:
            # A single SELECT is materialized to Parquet so the control plane can
            # page through its rows.
            conn.execute(f"COPY ({sql}) TO '{result_path}' (FORMAT PARQUET)")
            duration_ms = int((time.monotonic() - start) * 1000)
            row_count_result = conn.execute(
                f"SELECT count(*) FROM read_parquet('{result_path}')"
            ).fetchone()
            row_count = row_count_result[0] if row_count_result else 0
            # Size of the materialized result so the UI can show how large it is.
            if result_path.exists():
                result_bytes = result_path.stat().st_size
        else:
            # DDL/DML (and multi-statement scripts) produce no result grid. Run
            # the body directly: DuckDB returns an affected-row count for
            # INSERT/UPDATE/DELETE and no result set for pure DDL.
            affected = conn.execute(sql).fetchone()
            duration_ms = int((time.monotonic() - start) * 1000)
            row_count = affected[0] if affected and isinstance(affected[0], int) else 0
        result: dict[str, Any] = {
            "row_count": row_count,
            "duration_ms": duration_ms,
            "wrote_result": wrote_result,
            "result_bytes": result_bytes,
        }

        # When asked, compute true table stats on the same attached connection.
        # size_bytes has no reliable cross-backend source yet, so it stays null.
        if stats_for:
            schema = stats_for.get("schema")
            table = stats_for.get("table")
            if schema and table:
                try:
                    cnt = conn.execute(f'SELECT count(*) FROM "{schema}"."{table}"').fetchone()
                    result["table_row_count"] = cnt[0] if cnt else None
                except Exception as exc:  # noqa: BLE001 - stats are best-effort
                    logger.warning("Table stats failed for %s.%s: %s", schema, table, exc)
                    result["table_row_count"] = None
                result["table_size_bytes"] = None
                # Iceberg-native metadata for the table-detail page. Only
                # meaningful when a catalog is attached; best-effort throughout.
                if workspace_slug and polaris:
                    result["iceberg"] = _iceberg_metadata(conn, schema, table)
        return result
    finally:
        conn.close()
