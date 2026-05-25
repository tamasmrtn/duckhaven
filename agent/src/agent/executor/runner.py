"""DuckDB query runner.

M3 widening: `run_query_sync` accepts the workspace's backend
descriptor, the short-lived storage credentials minted by the control
plane, and the UC endpoint to ATTACH. Cloud backends get a connection-
scoped `CREATE SECRET` so the credentials die with the per-query
connection. Local backends skip secret creation entirely.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import duckdb

logger = logging.getLogger(__name__)


def _safe_install_load(conn: duckdb.DuckDBPyConnection, name: str) -> bool:
    """INSTALL + LOAD an extension; log + return False on failure."""
    try:
        conn.execute(f"INSTALL {name}")
        conn.execute(f"LOAD {name}")
        return True
    except Exception as exc:  # noqa: BLE001 - any failure is recoverable
        logger.warning("Failed to load %s: %s", name, exc)
        return False


def _apply_s3_secret(
    conn: duckdb.DuckDBPyConnection,
    secret_name: str,
    fields: dict[str, Any],
    scope: str | None,
) -> None:
    parts = ["TYPE S3"]
    params: list[Any] = []
    if (key := fields.get("access_key_id")) is not None:
        parts.append("KEY_ID ?")
        params.append(key)
    if (secret := fields.get("secret_access_key")) is not None:
        parts.append("SECRET ?")
        params.append(secret)
    if (token := fields.get("session_token")) is not None:
        parts.append("SESSION_TOKEN ?")
        params.append(token)
    if (region := fields.get("region")) is not None:
        parts.append("REGION ?")
        params.append(region)
    if scope:
        parts.append("SCOPE ?")
        params.append(scope)
    conn.execute(f"CREATE SECRET {secret_name} ({', '.join(parts)})", params)


def _apply_azure_secret(
    conn: duckdb.DuckDBPyConnection,
    secret_name: str,
    fields: dict[str, Any],
    scope: str | None,
) -> None:
    parts = ["TYPE AZURE"]
    params: list[Any] = []
    if (conn_str := fields.get("connection_string")) is not None:
        parts.append("CONNECTION_STRING ?")
        params.append(conn_str)
    elif (sas := fields.get("sas_token")) is not None:
        # UC may vend the SAS as a bare token; embed it as SHARED_ACCESS_SIGNATURE.
        parts.append("CHAIN ?")
        params.append("config")
        parts.append("SHARED_ACCESS_SIGNATURE ?")
        params.append(sas)
    if scope:
        parts.append("SCOPE ?")
        params.append(scope)
    conn.execute(f"CREATE SECRET {secret_name} ({', '.join(parts)})", params)


def _safe_secret_name(workspace_slug: str | None) -> str:
    """Produce a valid DuckDB identifier from the workspace slug."""
    if not workspace_slug:
        return "ws_default"
    cleaned = "".join(c if c.isalnum() else "_" for c in workspace_slug)
    return f"ws_{cleaned}" if cleaned else "ws_default"


def run_query_sync(
    sql: str,
    result_path: Path,
    memory_limit_gb: float,
    backend: dict[str, Any] | None = None,
    storage_credentials: dict[str, Any] | None = None,
    workspace_slug: str | None = None,
    uc_endpoint: str | None = None,
    on_connect: Callable[[duckdb.DuckDBPyConnection], None] | None = None,
) -> dict[str, int]:
    """Run a query through DuckDB and materialize the result to Parquet.

    Optional kwargs (passed by the control plane in M3):
    - `backend`: workspace storage backend descriptor `{kind, root_uri}`.
    - `storage_credentials`: short-lived creds `{kind, fields, expires_at}`.
    - `workspace_slug`: used as the UC catalog name and the secret name.
    - `uc_endpoint`: when set, ATTACH the workspace's UC catalog before
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
        backend_root = (backend or {}).get("root_uri")

        # Cloud backend: load the right extension and inject creds via SECRET
        # (the secret is connection-scoped, so it dies with this connection).
        if backend_kind == "s3":
            _safe_install_load(conn, "httpfs")
            if storage_credentials and storage_credentials.get("kind") == "s3":
                _apply_s3_secret(
                    conn,
                    _safe_secret_name(workspace_slug),
                    storage_credentials.get("fields") or {},
                    backend_root,
                )
        elif backend_kind == "adls_gen2":
            _safe_install_load(conn, "azure")
            if storage_credentials and storage_credentials.get("kind") == "azure":
                _apply_azure_secret(
                    conn,
                    _safe_secret_name(workspace_slug),
                    storage_credentials.get("fields") or {},
                    backend_root,
                )

        # Attach the workspace's UC catalog so the user's SQL can reference
        # tables by their UC names (e.g. SELECT * FROM main.events).
        if workspace_slug and uc_endpoint:
            if _safe_install_load(conn, "delta") and _safe_install_load(conn, "unity_catalog"):
                try:
                    conn.execute(
                        "ATTACH ? AS uc_attached (TYPE UC_CATALOG, ENDPOINT ?)",
                        [workspace_slug, uc_endpoint],
                    )
                    conn.execute("USE uc_attached")
                except Exception as exc:  # noqa: BLE001
                    logger.warning("UC ATTACH failed for %s: %s", workspace_slug, exc)

        start = time.monotonic()
        conn.execute(f"COPY ({sql}) TO '{result_path}' (FORMAT PARQUET)")
        duration_ms = int((time.monotonic() - start) * 1000)

        row_count_result = conn.execute(
            f"SELECT count(*) FROM read_parquet('{result_path}')"
        ).fetchone()
        row_count = row_count_result[0] if row_count_result else 0
        return {"row_count": row_count, "duration_ms": duration_ms}
    finally:
        conn.close()
