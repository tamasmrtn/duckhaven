"""DuckDB query runner.

`run_query_sync` accepts the workspace's catalog descriptors (each carrying its
Polaris warehouse name + storage backend) and the Polaris connection info, and
ATTACHes every catalog under its slug alias (multi-attach) so queries can join
across `catalog.schema.table`. DuckDB's `iceberg` extension performs the OAuth2
client-credentials exchange itself and, for cloud backends, obtains short-lived
storage credentials from Polaris via access delegation — so the runner injects
no storage secrets of its own.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import certifi
import duckdb

from agent.executor.plan import parse_profile

logger = logging.getLogger(__name__)


def _configure_external_tls(conn: duckdb.DuckDBPyConnection, *, azure: bool) -> None:
    """Give the azure/httpfs extensions a CA bundle so HTTPS to the cloud works.

    DuckDB's statically-linked extensions don't know the distro's CA path, so
    TLS to Azure Blob / S3 fails with an "SSL CA cert" error in a minimal
    container (plain-HTTP MinIO never hits this). certifi ships a portable
    bundle; point ``ca_cert_file`` at it. The azure extension only honours it
    under the curl transport, so select that too."""
    bundle = certifi.where().replace("'", "''")
    try:
        conn.execute(f"SET ca_cert_file = '{bundle}'")
        if azure:
            conn.execute("SET azure_transport_option_type = 'curl'")
    except duckdb.Error as exc:
        logger.warning("Could not configure TLS CA bundle: %s", exc)


# Curated profile metric set (see Part 2). Keys are DuckDB metric names; the
# value "true" enables each. Captured per operator + per query, then parsed by
# the shared tree-walker into the normalized profile shape.
_PROFILE_METRICS = (
    "OPERATOR_TYPE",
    "OPERATOR_NAME",
    "OPERATOR_CARDINALITY",
    "OPERATOR_ROWS_SCANNED",
    "OPERATOR_TIMING",
    "RESULT_SET_SIZE",
    "EXTRA_INFO",
    "CPU_TIME",
    "LATENCY",
    "ROWS_RETURNED",
    "CUMULATIVE_ROWS_SCANNED",
    "SYSTEM_PEAK_BUFFER_MEMORY",
    "SYSTEM_PEAK_TEMP_DIR_SIZE",
    "BLOCKED_THREAD_TIME",
    "TOTAL_BYTES_READ",
    "TOTAL_BYTES_WRITTEN",
)
_PROFILE_SETTINGS_JSON = json.dumps({m: "true" for m in _PROFILE_METRICS})

# Fixed identifier for the per-connection iceberg OAuth2 secret. Each catalog is
# ATTACHed under its own slug alias (multi-attach), not a single fixed alias.
_ICEBERG_SECRET = "dh_iceberg"
# Per-connection secret carrying the active trace's W3C traceparent, scoped to
# the Polaris endpoint. DuckDB's REST catalog client (iceberg/httpfs) has no
# OpenTelemetry instrumentation of its own, so without this every Polaris call
# DuckDB makes directly (OAuth token exchange, namespace/table lookups,
# credential vending) would start a disconnected trace instead of joining the
# query's. Scoping to the Polaris endpoint keeps it off unrelated S3/ADLS calls.
_TRACE_HEADERS_SECRET = "dh_trace_headers"
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

# Substrings that identify a rejected/expired *storage* credential (as opposed to
# a genuine authz or missing-object error). Polaris vends short-lived STS creds
# (an hour on the bundled MinIO); once they expire the object store purges the
# temporary access key and returns "InvalidAccessKeyId" ("...does not exist..."),
# or "InvalidToken"/"ExpiredToken" for the session token. When we see one of
# these we re-vend a fresh credential and retry once rather than surfacing a
# confusing S3 error to the user (G-D-cred-refresh).
_CREDENTIAL_ERROR_MARKERS = (
    "access key id you provided does not exist",
    "invalidaccesskeyid",
    "the security token included in the request is invalid",
    "invalidtoken",
    "expiredtoken",
    "token has expired",
)


def _is_credential_error(exc: Exception) -> bool:
    """True when ``exc`` looks like an expired/rejected vended storage credential."""
    msg = str(exc).lower()
    return any(marker in msg for marker in _CREDENTIAL_ERROR_MARKERS)


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


def _iceberg_metadata(
    conn: duckdb.DuckDBPyConnection, catalog: str, schema: str, table: str
) -> dict[str, Any]:
    """Best-effort Iceberg-native metadata for a table in the attached catalog.

    Returns the current snapshot id + timestamp, the data-file count, and a
    has-deletes flag (true when the table carries position/equality delete files
    — the same probe the future merge-on-read read guard will use). Each field is
    independently best-effort: a probe failure (e.g. an older `iceberg` extension
    lacking a function) degrades that field to None rather than failing the
    query. The catalog must already be ATTACHed (under its slug alias).
    """
    ident = f'"{catalog}"."{schema}"."{table}"'
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


def _iceberg_columns(conn: duckdb.DuckDBPyConnection, ident: str) -> list[str]:
    """Column names exposed by ``iceberg_metadata`` for this extension version."""
    return [
        d[0] for d in conn.execute(f"SELECT * FROM iceberg_metadata({ident}) LIMIT 0").description
    ]


def collect_table_health(
    conn: duckdb.DuckDBPyConnection,
    catalog: str,
    schema: str,
    table: str,
    *,
    target_file_bytes: int,
    include_orphans: bool = False,
) -> dict[str, Any]:
    """Best-effort health metrics for one table in the attached catalog.

    Sibling of ``_iceberg_metadata`` but richer: it derives file-size distribution,
    snapshot/manifest counts, and (on the deep tier) an orphan-file estimate, all
    from DuckDB's ``iceberg`` extension over the already-attached catalog. Every
    field is independently best-effort — a probe failure degrades that field to
    ``None`` rather than failing the scan. ``schema``/``table`` are echoed so the
    control plane's frame handler can route the sample without extra state.

    Orphan detection (``include_orphans``) lists the table's data directory with
    ``glob`` and diffs against the live data-file set; it is expensive at scale and
    only an estimate (in-flight writes look orphaned), so it runs on a slow cadence.
    """
    ident = f'"{catalog}"."{schema}"."{table}"'
    health: dict[str, Any] = {
        "catalog": catalog,
        "schema": schema,
        "table": table,
        "snapshot_count": None,
        "data_file_count": None,
        "manifest_count": None,
        "total_data_bytes": None,
        "avg_file_bytes": None,
        "small_file_ratio": None,
        "metadata_bytes": None,
        "orphan_file_count": None,
        "orphan_bytes": None,
    }

    try:
        row = conn.execute(f"SELECT count(*) FROM iceberg_snapshots({ident})").fetchone()
        health["snapshot_count"] = int(row[0]) if row else None
    except Exception as exc:  # noqa: BLE001 - metadata is best-effort
        logger.warning("iceberg_snapshots count failed for %s.%s: %s", schema, table, exc)

    live_paths: list[str] = []
    try:
        columns = _iceberg_columns(conn, ident)
        classify = "manifest_content" if "manifest_content" in columns else "content"
        # The data-file size column name has varied across extension versions.
        size_col = next(
            (c for c in ("file_size_in_bytes", "file_size_bytes", "file_size") if c in columns),
            None,
        )
        size_expr = size_col or "NULL"
        rows = conn.execute(
            f"SELECT file_path, manifest_path, {size_expr} AS sz "
            f"FROM iceberg_metadata({ident}) WHERE {classify} = 'DATA'"
        ).fetchall()
        manifests = {r[1] for r in rows if r[1] is not None}
        sizes = [int(r[2]) for r in rows if r[2] is not None]
        live_paths = [r[0] for r in rows if r[0] is not None]
        health["data_file_count"] = len(rows)
        health["manifest_count"] = len(manifests) or None
        # DuckDB's iceberg extension (through 1.5.3) does not expose a data-file
        # size column, so when it is absent fall back to the Parquet footers. This
        # reads one footer per file, so it runs only on the deep tier alongside the
        # orphan scan to keep the cheap cadence free of per-file object reads.
        if not sizes and include_orphans and live_paths:
            sizes = _parquet_file_sizes(conn, live_paths)
        if sizes:
            total = sum(sizes)
            health["total_data_bytes"] = total
            health["avg_file_bytes"] = total // len(sizes)
            small = sum(1 for s in sizes if s < target_file_bytes)
            health["small_file_ratio"] = round(small / len(sizes), 4)
    except Exception as exc:  # noqa: BLE001 - metadata is best-effort
        logger.warning("iceberg_metadata aggregate failed for %s.%s: %s", schema, table, exc)

    if include_orphans and live_paths:
        health.update(_orphan_estimate(conn, live_paths, health.get("avg_file_bytes")))
    return health


def _parquet_file_sizes(conn: duckdb.DuckDBPyConnection, paths: list[str]) -> list[int]:
    """Per-file sizes read from the Parquet footers, for iceberg-extension versions
    that don't surface a size column in ``iceberg_metadata``. One ranged read per
    file (hence deep-tier only); ``total_compressed_size`` omits the footer/header
    but is well within tolerance for small-file detection against the target size.
    """
    try:
        rows = conn.execute(
            "SELECT file_name, sum(total_compressed_size) AS sz "
            "FROM parquet_metadata($files) GROUP BY file_name",
            {"files": paths},
        ).fetchall()
    except Exception as exc:  # noqa: BLE001 - the size probe is best-effort
        logger.warning("parquet_metadata size probe failed: %s", exc)
        return []
    return [int(sz) for _, sz in rows if sz is not None]


def _orphan_estimate(
    conn: duckdb.DuckDBPyConnection, live_paths: list[str], avg_file_bytes: int | None
) -> dict[str, Any]:
    """Count files under the data directory not referenced by current metadata.

    Derives the data prefix from a live file path (no need to resolve the table
    location separately), lists it with ``glob``, and subtracts the live set.
    ``glob`` yields no sizes, so orphan bytes are estimated from the live average.
    """
    out: dict[str, Any] = {"orphan_file_count": None, "orphan_bytes": None}
    sample = live_paths[0]
    marker = "/data/"
    if marker not in sample:
        return out
    data_dir = sample[: sample.index(marker) + len(marker)]
    pattern = f"{data_dir}**".replace("'", "''")
    try:
        listed = {r[0] for r in conn.execute(f"SELECT file FROM glob('{pattern}')").fetchall()}
    except Exception as exc:  # noqa: BLE001 - listing is best-effort
        logger.warning("glob orphan scan failed for %s: %s", data_dir, exc)
        return out
    orphans = listed - set(live_paths)
    out["orphan_file_count"] = len(orphans)
    if avg_file_bytes:
        out["orphan_bytes"] = len(orphans) * avg_file_bytes
    return out


def _attach_catalogs(
    conn: duckdb.DuckDBPyConnection,
    *,
    catalogs: list[dict[str, Any]],
    active_catalog: str | None,
    polaris: dict[str, Any],
    trace_headers: dict[str, str] | None = None,
) -> None:
    """Create the iceberg OAuth2 secret and ATTACH every catalog (multi-attach).

    Each catalog is attached under its slug alias so the user's SQL can address
    `catalog.schema.table` and join across catalogs; the active catalog is then
    `USE`d so unqualified names resolve. DuckDB exchanges the client credentials
    for a token itself; with `vended_credentials` Polaris also vends scoped
    storage creds on access. Per-catalog ATTACH is best-effort: one bad catalog
    is logged and skipped rather than failing the whole query.
    """
    endpoint = str(polaris["endpoint"]).rstrip("/")
    # `trace_headers` carries the caller's active span (handle_dispatch, or
    # duckdb.execute for static profiles) onto every DuckDB-issued request to
    # Polaris, so Polaris's spans join this query's trace instead of starting
    # their own. It must be captured on the event-loop thread by the caller —
    # this function runs inside a worker thread (via run_in_executor), where
    # OpenTelemetry's contextvar-based "current span" is not propagated, so
    # calling inject_trace_context() here would silently see no active span.
    # None when no SDK is configured or no span was active: DuckDB behaves
    # exactly as before.
    if trace_headers:
        conn.execute(
            f"CREATE OR REPLACE SECRET {_TRACE_HEADERS_SECRET} "
            "(TYPE HTTP, EXTRA_HTTP_HEADERS ?, SCOPE ?)",
            [trace_headers, endpoint],
        )
    conn.execute(
        f"CREATE SECRET {_ICEBERG_SECRET} "
        "(TYPE ICEBERG, CLIENT_ID ?, CLIENT_SECRET ?, OAUTH2_SERVER_URI ?)",
        [
            polaris["client_id"],
            polaris["client_secret"],
            f"{endpoint}/api/catalog/v1/oauth/tokens",
        ],
    )
    # ATTACH does not accept bind parameters, so inline the warehouse name, alias
    # and endpoint as quoted literals (quotes escaped). None are user-supplied SQL
    # (slug/polaris_name come from the control plane; endpoint from agent config).
    cat_endpoint = f"{endpoint}/api/catalog".replace("'", "''")
    active = None
    for cat in catalogs:
        slug = cat["slug"]
        kind = (cat.get("backend") or {}).get("kind")
        delegation = "vended_credentials" if kind in _VENDED_BACKENDS else "none"
        wh = str(cat["polaris_name"]).replace("'", "''")
        alias = slug.replace('"', '""')
        try:
            conn.execute(
                f"ATTACH '{wh}' AS \"{alias}\" "
                f"(TYPE ICEBERG, SECRET {_ICEBERG_SECRET}, ENDPOINT '{cat_endpoint}', "
                f"ACCESS_DELEGATION_MODE '{delegation}')"
            )
        except Exception as exc:  # noqa: BLE001 - one bad catalog must not fail the query
            logger.warning("Polaris ATTACH failed for catalog %s: %s", slug, exc)
            continue
        if slug == active_catalog:
            active = cat
    # `USE <catalog>.<schema>` sets the default catalog (so unqualified SQL
    # resolves) and a default schema. A bare `USE <catalog>` does not reliably
    # resolve the attached Iceberg catalog's namespaces.
    if active is None and catalogs:
        active = catalogs[0]
    if active is not None:
        schema = (active.get("default_schema") or _DEFAULT_NAMESPACE).replace('"', '""')
        aslug = active["slug"].replace('"', '""')
        conn.execute(f'USE "{aslug}"."{schema}"')


def open_and_attach(
    *,
    catalogs: list[dict[str, Any]] | None = None,
    active_catalog: str | None = None,
    polaris: dict[str, Any] | None = None,
    trace_headers: dict[str, str] | None = None,
) -> duckdb.DuckDBPyConnection:
    """Open a DuckDB connection, load the storage IO extensions, and ATTACH every
    catalog bound to the workspace so table names bind.

    Loads the union of IO extensions across the catalogs' backends (a workspace
    may mix S3 and ADLS catalogs). Shared by the cost estimator (pre-execution
    EXPLAIN) and the runner: in the ``auto`` profile a single connection is opened
    here, estimated against, then handed to ``run_query_sync`` for execution +
    profiling (one attach / one OAuth exchange).

    `trace_headers`: a W3C traceparent carrier (see `_attach_catalogs`) captured
    by the caller on the event-loop thread, since this function runs on a
    worker thread via `run_in_executor` where OpenTelemetry's current-span
    context is not available.
    """
    conn = duckdb.connect()
    catalogs = catalogs or []
    backend_kinds = {(cat.get("backend") or {}).get("kind") for cat in catalogs}
    for kind in backend_kinds:
        if (io_ext := _BACKEND_IO_EXTENSION.get(kind or "")) is not None:
            _safe_install_load(conn, io_ext)
    # External cloud backends (s3/adls_gen2) talk HTTPS and need a CA bundle the
    # statically-linked extensions can't find on their own.
    if backend_kinds - {None, "object_store"}:
        _configure_external_tls(conn, azure="adls_gen2" in backend_kinds)
    if catalogs and polaris and _safe_install_load(conn, "iceberg"):
        try:
            _attach_catalogs(
                conn,
                catalogs=catalogs,
                active_catalog=active_catalog,
                polaris=polaris,
                trace_headers=trace_headers,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Polaris ATTACH failed: %s", exc)
    return conn


def _capture_profile(conn: duckdb.DuckDBPyConnection, profile_path: Path) -> dict[str, Any] | None:
    """Read + normalize the DuckDB JSON profile written to ``profile_path``.

    Best-effort (mirrors ``_iceberg_metadata``): any failure returns ``None``.
    """
    try:
        raw = json.loads(profile_path.read_text())
        summary, tree = parse_profile(raw)
        return {"summary": summary.to_dict(), "tree": tree.to_dict()}
    except Exception as exc:  # noqa: BLE001 - profiling is best-effort
        logger.warning("Profile capture failed: %s", exc)
        return None


def run_query_sync(
    sql: str,
    result_path: Path,
    *,
    memory_bytes: int,
    threads: int,
    catalogs: list[dict[str, Any]] | None = None,
    active_catalog: str | None = None,
    polaris: dict[str, Any] | None = None,
    stats_for: dict[str, str] | None = None,
    health_for: dict[str, Any] | None = None,
    conn: duckdb.DuckDBPyConnection | None = None,
    enable_profiling: bool = True,
    on_connect: Callable[[duckdb.DuckDBPyConnection], None] | None = None,
    trace_headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Run a query through DuckDB.

    A single `SELECT` is materialized to Parquet (`wrote_result=True`); any other
    statement — DDL or DML, including multi-statement scripts — is executed
    directly with no result file (`wrote_result=False`).

    Optional kwargs (passed by the control plane):
    - `catalogs`: the workspace's catalog descriptors (each `{slug, polaris_name,
      backend, default_schema}`); all are ATTACHed (multi-attach).
    - `active_catalog`: slug `USE`d for unqualified table names.
    - `polaris`: `{endpoint, client_id, client_secret}`. When set together with
      `catalogs`, ATTACH them before running the user SQL.
    - `conn`: a pre-opened+attached connection (the `auto` profile reuses the
      one it ran EXPLAIN on). When omitted, the runner opens and attaches its own.
    - `enable_profiling`: capture DuckDB's JSON profile for a materialized SELECT
      and return it under `result["profile"]` (best-effort).
    - `on_connect`: called with the connection so the supervisor can
      `interrupt()` it on timeout/cancel (G-D2-a).
    - `trace_headers`: forwarded to `open_and_attach` when this call opens its
      own connection (ignored when `conn` is already attached).
    """

    def _open_fresh() -> duckdb.DuckDBPyConnection:
        c = open_and_attach(
            catalogs=catalogs,
            active_catalog=active_catalog,
            polaris=polaris,
            trace_headers=trace_headers,
        )
        if on_connect is not None:
            on_connect(c)
        return c

    if conn is None:
        conn = _open_fresh()
    elif on_connect is not None:
        on_connect(conn)
    # Sibling of the result file; retention only sweeps `*.parquet`, so we own
    # this file's lifecycle and unlink it ourselves.
    profile_path = result_path.with_suffix(".profile.json")
    # We can transparently re-vend credentials only when we know how to re-ATTACH.
    can_reattach = bool(catalogs and polaris)

    def _execute() -> dict[str, Any]:
        # The admission manager sizes each query's slice of the agent's budget
        # (memory_bytes + threads) so concurrent sessions never oversubscribe the
        # cgroup memory limit. DuckDB's default thread count ignores the cgroup
        # CPU quota, so `threads` is set explicitly.
        mem_gb = memory_bytes / 1024**3
        conn.execute(f"SET memory_limit='{mem_gb}GB'")
        conn.execute(f"SET threads={threads}")

        start = time.monotonic()
        wrote_result = _is_single_select(sql)
        result_bytes: int | None = None
        profile: dict[str, Any] | None = None
        if wrote_result:
            # A single SELECT is materialized to Parquet so the control plane can
            # page through its rows. Profile only this path so DDL/DML carry no
            # profile (the UI shows a no-profile state for them).
            if enable_profiling:
                conn.execute("PRAGMA enable_profiling='json'")
                conn.execute(f"PRAGMA profiling_output='{profile_path}'")
                conn.execute(f"PRAGMA custom_profiling_settings='{_PROFILE_SETTINGS_JSON}'")
            conn.execute(f"COPY ({sql}) TO '{result_path}' (FORMAT PARQUET)")
            duration_ms = int((time.monotonic() - start) * 1000)
            if enable_profiling:
                conn.execute("PRAGMA disable_profiling")
                profile = _capture_profile(conn, profile_path)
            row_count_result = conn.execute(
                f"SELECT count(*) FROM read_parquet('{result_path}')"
            ).fetchone()
            row_count = row_count_result[0] if row_count_result else 0
            # DuckDB's profile reports the COPY's returned-row count (1), not the
            # SELECT's result size. Surface the real result row count so the UI's
            # summary and the scan-blow-up heuristic compare against it. Also
            # record the admission reservation this query ran under so the UI can
            # show actual peak/spill against what it was granted.
            if profile is not None:
                profile["summary"]["rows_returned"] = row_count
                profile["summary"]["reserved_memory_bytes"] = memory_bytes
                profile["summary"]["reserved_threads"] = threads
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
            "profile": profile,
        }

        # When asked, compute true table stats on the same attached connection.
        # size_bytes has no reliable cross-backend source yet, so it stays null.
        if stats_for:
            catalog = stats_for.get("catalog")
            schema = stats_for.get("schema")
            table = stats_for.get("table")
            if catalog and schema and table:
                try:
                    cnt = conn.execute(
                        f'SELECT count(*) FROM "{catalog}"."{schema}"."{table}"'
                    ).fetchone()
                    result["table_row_count"] = cnt[0] if cnt else None
                except Exception as exc:  # noqa: BLE001 - stats are best-effort
                    logger.warning(
                        "Table stats failed for %s.%s.%s: %s", catalog, schema, table, exc
                    )
                    result["table_row_count"] = None
                result["table_size_bytes"] = None
                # Iceberg-native metadata for the table-detail page. Only
                # meaningful when a catalog is attached; best-effort throughout.
                if catalogs and polaris:
                    result["iceberg"] = _iceberg_metadata(conn, catalog, schema, table)

        # Maintenance health probe: richer Iceberg metrics on the same attached
        # connection. Driven by the scanner; best-effort throughout.
        if health_for and catalogs and polaris:
            catalog = health_for.get("catalog")
            schema = health_for.get("schema")
            table = health_for.get("table")
            if catalog and schema and table:
                try:
                    result["health"] = collect_table_health(
                        conn,
                        catalog,
                        schema,
                        table,
                        target_file_bytes=int(health_for.get("target_file_bytes", 128 * 1024**2)),
                        include_orphans=bool(health_for.get("include_orphans", False)),
                    )
                except Exception as exc:  # noqa: BLE001 - health probe is best-effort
                    logger.warning("Health probe failed for %s.%s: %s", schema, table, exc)
        return result

    try:
        try:
            return _execute()
        except Exception as exc:  # noqa: BLE001 - re-vend once on credential expiry
            if not (can_reattach and _is_credential_error(exc)):
                raise
            logger.warning(
                "Storage credentials rejected mid-query; re-vending and retrying once: %s",
                exc,
            )
            conn.close()
            conn = _open_fresh()
            return _execute()
    finally:
        conn.close()
        profile_path.unlink(missing_ok=True)
