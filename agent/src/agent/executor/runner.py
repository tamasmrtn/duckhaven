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
from pathlib import Path
from typing import Any

import duckdb

logger = logging.getLogger(__name__)

# Fixed identifiers for the per-connection iceberg secret and attached catalog.
_ICEBERG_SECRET = "dh_iceberg"
_CATALOG_ALIAS = "dh_catalog"

# Backend kind -> DuckDB storage-IO extension (loaded so DuckDB can read/write
# the object store with the credentials Polaris vends). Local FS needs none.
_BACKEND_IO_EXTENSION: dict[str, str] = {"s3": "httpfs", "adls_gen2": "azure"}
# Backends whose data lives in an object store get vended credentials from
# Polaris; local filesystem backends need none.
_VENDED_BACKENDS = {"s3", "adls_gen2"}


def _safe_install_load(conn: duckdb.DuckDBPyConnection, name: str) -> bool:
    """INSTALL + LOAD an extension; log + return False on failure."""
    try:
        conn.execute(f"INSTALL {name}")
        conn.execute(f"LOAD {name}")
        return True
    except Exception as exc:  # noqa: BLE001 - any failure is recoverable
        logger.warning("Failed to load %s: %s", name, exc)
        return False


def _attach_polaris(
    conn: duckdb.DuckDBPyConnection,
    *,
    warehouse: str,
    polaris: dict[str, Any],
    delegation_mode: str,
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
    conn.execute(
        f"ATTACH ? AS {_CATALOG_ALIAS} (TYPE ICEBERG, SECRET {_ICEBERG_SECRET}, "
        f"ENDPOINT ?, ACCESS_DELEGATION_MODE '{delegation_mode}')",
        [warehouse, f"{endpoint}/api/catalog"],
    )
    conn.execute(f"USE {_CATALOG_ALIAS}")


def run_query_sync(
    sql: str,
    result_path: Path,
    memory_limit_gb: float,
    backend: dict[str, Any] | None = None,
    workspace_slug: str | None = None,
    polaris: dict[str, Any] | None = None,
    stats_for: dict[str, str] | None = None,
    on_connect: Callable[[duckdb.DuckDBPyConnection], None] | None = None,
) -> dict[str, Any]:
    """Run a query through DuckDB and materialize the result to Parquet.

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
        conn.execute(f"SET memory_limit='{memory_limit_gb}GB'")

        backend_kind = (backend or {}).get("kind")

        # Load the storage-IO extension so DuckDB can read/write the object
        # store with the credentials Polaris vends. Local FS needs none.
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
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Polaris ATTACH failed for %s: %s", workspace_slug, exc)

        start = time.monotonic()
        conn.execute(f"COPY ({sql}) TO '{result_path}' (FORMAT PARQUET)")
        duration_ms = int((time.monotonic() - start) * 1000)

        row_count_result = conn.execute(
            f"SELECT count(*) FROM read_parquet('{result_path}')"
        ).fetchone()
        row_count = row_count_result[0] if row_count_result else 0
        result: dict[str, Any] = {"row_count": row_count, "duration_ms": duration_ms}

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
        return result
    finally:
        conn.close()
