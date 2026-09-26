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
import re
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
    container (the plain-HTTP bundled store never hits this). certifi ships a portable
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
    # Per statement, unlike the two SYSTEM_PEAK_* marks above — the only DuckDB
    # memory metric that is. See _apply_watermarks.
    "TOTAL_MEMORY_ALLOCATED",
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
# storage: object_store is backed by the bundled object store (S3), needing httpfs.
_BACKEND_IO_EXTENSION: dict[str, str] = {
    "object_store": "httpfs",
    "s3": "httpfs",
    "adls_gen2": "azure",
}
# All backends are object storage, so all get vended credentials from Polaris.
_VENDED_BACKENDS = {"object_store", "s3", "adls_gen2"}

# Catalog kind -> extensions needed to attach it. Mirrors
# api/services/agent_capabilities.py; `postgres` is the install name.
_CATALOG_KIND_EXTENSIONS: dict[str, tuple[str, ...]] = {
    "iceberg_polaris": ("iceberg",),
    "ducklake": ("ducklake", "postgres"),
}
KIND_DUCKLAKE = "ducklake"


# Suffixed by slug: one connection can attach several DuckLake catalogs.
def _meta_secret(slug: str) -> str:
    return f"dh_dl_meta_{slug}"


def _storage_secret(slug: str) -> str:
    return f"dh_dl_store_{slug}"


# Substrings that identify a rejected/expired *storage* credential (as opposed to
# a genuine authz or missing-object error). Polaris vends short-lived STS creds
# (an hour on the bundled store); once they expire the object store purges the
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
    """True when the body is exactly one row-producing statement — the only shape
    we materialize to Parquet. Everything else (DDL/DML, multi-statement scripts)
    is executed directly and produces no result file.

    Note this is broader than a literal `SELECT`: DuckDB also reports `DESCRIBE`,
    `SHOW`, `SUMMARIZE` and the row-returning `PRAGMA`s as `StatementType.SELECT`
    (a config-setting `PRAGMA x = y` is typed `SET`, not SELECT). They all return
    a result grid, so they all belong on the materialize path — see
    `_run_one_statement` for why that path cannot use `COPY (…) TO`."""
    try:
        statements = duckdb.extract_statements(sql)
    except Exception:  # noqa: BLE001 - a parse failure surfaces when executed
        return False
    return len(statements) == 1 and statements[0].type == duckdb.StatementType.SELECT


# Statement types that touch no data and need no memory beyond the connection
# itself. Everything else that is not a single SELECT — DDL, DML, multi-statement
# scripts — keeps the fallback bucket, because those genuinely are not cheap (an
# Iceberg `CREATE TABLE … AS SELECT` needs a ~76 MiB Parquet row-group buffer in
# one allocation however few rows it writes). `ANALYZE` is deliberately not here
# — see `is_cheap_statement`, DuckDB doesn't type it as its own statement type.
_CHEAP_STATEMENT_TYPES = frozenset(
    getattr(duckdb.StatementType, name)
    for name in ("SET", "TRANSACTION")
    if hasattr(duckdb.StatementType, name)
)


def is_cheap_statement(sql: str) -> bool:
    """True when ``sql`` cannot need more than a session's idle baseline.

    `USE`/`SET` are typed ``SET`` by DuckDB. They were being charged the
    unestimable-statement fallback bucket — a third of the whole agent — which on
    a 22-way burst meant every session claimed a third of the budget to run a
    one-millisecond statement, fragmenting it before a single real query started.

    `ANALYZE` has the same problem but can't be caught by statement type alone:
    DuckDB types both bare `ANALYZE` and `ANALYZE <table>` as
    ``StatementType.VACUUM`` (verified against 1.5.5), the same type a real
    `VACUUM` gets — and a real `VACUUM` is not necessarily cheap, so it's not
    safe to treat the whole type as cheap. Disambiguate on the statement's own
    leading keyword instead.
    """
    try:
        statements = duckdb.extract_statements(sql)
    except Exception:  # noqa: BLE001 - a parse failure surfaces when executed
        return False
    if len(statements) != 1:
        return False
    stmt = statements[0]
    if stmt.type in _CHEAP_STATEMENT_TYPES:
        return True
    return stmt.type == duckdb.StatementType.VACUUM and sql.strip().lower().startswith("analyze")


def _safe_install_load(conn: duckdb.DuckDBPyConnection, name: str) -> bool:
    """INSTALL + LOAD an extension; log + return False on failure."""
    try:
        conn.execute(f"INSTALL {name}")
        conn.execute(f"LOAD {name}")
        return True
    except Exception as exc:  # noqa: BLE001 - any failure is recoverable
        logger.warning("Failed to load %s: %s", name, exc)
        return False


def _enable_connection_caching(conn: duckdb.DuckDBPyConnection) -> None:
    """Reuse HTTP connections to object storage instead of dialing one per request.

    `httpfs_connection_caching` defaults to **false**, so DuckDB opens a fresh TCP
    connection for every object it reads and leaves it in TIME_WAIT. A scan of one
    SF10 Iceberg table is ~500 objects, so a burst of concurrent reads exhausts the
    container's entire ephemeral port range (32768-60999, 28,232 ports) in seconds.
    Every connection then fails with EADDRNOTAVAIL, which DuckDB surfaces as
    "Could not connect to server" — and because the throw can land on a background
    thread with nothing to catch it, it can take the whole agent process down via
    std::terminate.

    Measured, 18 concurrent readers x 3 rounds against the bundled store: 18/54
    statements succeeded with 28,231 sockets in TIME_WAIT; with caching on, 54/54
    and 108. At 32 concurrent x 5 rounds, 160/160.

    `http_keep_alive` alone does not do this — it governs the header, while this
    governs whether the client is kept at all.
    """
    try:
        conn.execute("SET httpfs_connection_caching = true")
    except Exception as exc:  # noqa: BLE001 - older httpfs may not know the setting
        logger.warning("Could not enable httpfs connection caching: %s", exc)


def _iceberg_metadata(
    conn: duckdb.DuckDBPyConnection, catalog: str, schema: str, table: str
) -> dict[str, Any]:
    """Best-effort Iceberg-native metadata for a table in the attached catalog.

    Returns snapshot id and timestamp, data-file count and bytes, and a
    has-deletes flag.
    Each field degrades to None on its own probe failure. The catalog must be
    ATTACHed already.
    """
    ident = f'"{catalog}"."{schema}"."{table}"'
    meta: dict[str, Any] = {
        "snapshot_id": None,
        "snapshot_at": None,
        "data_file_count": None,
        "data_file_size_bytes": None,
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
        # Newer iceberg extensions moved data-vs-delete classification from
        # `content` to `manifest_content`. Both columns exist in the new schema,
        # so inspect which one the running extension exposes.
        columns = _iceberg_columns(conn, ident)
        classify = "manifest_content" if "manifest_content" in columns else "content"
        # The size column name has varied across extension versions.
        size_col = next(
            (c for c in ("file_size_in_bytes", "file_size_bytes", "file_size") if c in columns),
            None,
        )
        rows = conn.execute(
            f"SELECT {classify}, count(*), sum({size_col or 'NULL'}) "
            f"FROM iceberg_metadata({ident}) GROUP BY {classify}"
        ).fetchall()
        if rows:
            counts = {str(content): n for content, n, _ in rows}
            meta["data_file_count"] = counts.get("DATA", 0)
            data_bytes = next((b for content, _, b in rows if str(content) == "DATA"), None)
            if data_bytes is not None:
                meta["data_file_size_bytes"] = int(data_bytes)
            elif size_col is not None and "DATA" not in counts:
                meta["data_file_size_bytes"] = 0
            meta["has_deletes"] = any(
                key in counts for key in ("DELETE", "POSITION_DELETES", "EQUALITY_DELETES")
            )
    except Exception as exc:  # noqa: BLE001 - metadata is best-effort
        logger.warning("iceberg_metadata failed for %s.%s: %s", schema, table, exc)
    return meta


def _ducklake_metadata(
    conn: duckdb.DuckDBPyConnection,
    catalog: str,
    schema: str,
    table: str,
) -> dict[str, Any]:
    """Best-effort DuckLake-native metadata for a table in the attached catalog.

    Snapshot fields stay None: DuckLake snapshots are catalog-wide, and the
    control plane derives which touched a table. `has_deletes` ignores inlined
    deletes.
    """
    meta: dict[str, Any] = {
        "snapshot_id": None,
        "snapshot_at": None,
        "data_file_count": None,
        "has_deletes": None,
    }
    try:
        row = conn.execute(
            "SELECT count(*), coalesce(bool_or(delete_file IS NOT NULL), false) "
            "FROM ducklake_list_files(?, ?, schema => ?)",
            [catalog, table, schema],
        ).fetchone()
        if row:
            meta["data_file_count"] = row[0]
            meta["has_deletes"] = bool(row[1])
    except Exception as exc:  # noqa: BLE001 - metadata is best-effort
        logger.warning("ducklake_list_files failed for %s.%s: %s", schema, table, exc)
    return meta


def _catalog_kind(catalogs: list[dict[str, Any]], slug: str | None) -> str:
    """The catalog kind of an attached catalog, by its slug alias."""
    for cat in catalogs:
        if cat.get("slug") == slug:
            return str(cat.get("kind") or "iceberg_polaris")
    return "iceberg_polaris"


def _catalog_metadata_schema(catalogs: list[dict[str, Any]], slug: str | None) -> str | None:
    """The Postgres schema holding a DuckLake catalog's own tables, by slug."""
    for cat in catalogs:
        if cat.get("slug") == slug:
            value = cat.get("metadata_schema")
            return str(value) if value else None
    return None


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

    File-size distribution, snapshot/manifest counts and (on the deep tier) an
    orphan estimate, from DuckDB's ``iceberg`` extension. Each field degrades to
    None on its own probe failure. ``schema``/``table`` are echoed for routing.

    Orphan detection globs the table directory and diffs against the live file
    set; expensive and only an estimate, so it runs on a slow cadence.
    """
    ident = f'"{catalog}"."{schema}"."{table}"'
    health: dict[str, Any] = {
        "catalog": catalog,
        "schema": schema,
        "table": table,
        "snapshot_count": None,
        "snapshot_id": None,
        "oldest_snapshot_age_days": None,
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

    try:
        latest = conn.execute(
            f"SELECT snapshot_id FROM iceberg_snapshots({ident}) "
            "ORDER BY sequence_number DESC LIMIT 1"
        ).fetchone()
        if latest:
            health["snapshot_id"] = latest[0]
        oldest = conn.execute(
            f"SELECT min(timestamp_ms) FROM iceberg_snapshots({ident})"
        ).fetchone()
        # epoch-millis on older extensions, TIMESTAMP on newer ones.
        oldest_ts = oldest[0] if oldest else None
        if isinstance(oldest_ts, datetime):
            oldest_dt = oldest_ts if oldest_ts.tzinfo else oldest_ts.replace(tzinfo=UTC)
            age_days = (datetime.now(UTC) - oldest_dt).total_seconds() / 86_400
            health["oldest_snapshot_age_days"] = round(age_days, 4)
        elif isinstance(oldest_ts, (int, float)):
            age_ms = int(time.time() * 1000) - int(oldest_ts)
            health["oldest_snapshot_age_days"] = round(age_ms / 86_400_000, 4)
    except Exception as exc:  # noqa: BLE001 - metadata is best-effort
        logger.warning("iceberg_snapshots age failed for %s.%s: %s", schema, table, exc)

    live_paths: list[str] = []
    manifests: set[str] = set()
    try:
        columns = _iceberg_columns(conn, ident)
        classify = "manifest_content" if "manifest_content" in columns else "content"
        # The size column name has varied across extension versions.
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
        # No size column: fall back to Parquet footers on the deep tier only
        # (one read per file). Wide tables are sampled, then scaled.
        sampled = False
        if not sizes and include_orphans and live_paths:
            sizes, sampled = _parquet_file_sizes(conn, live_paths)
        if sizes:
            total = sum(sizes)
            health["avg_file_bytes"] = total // len(sizes)
            health["total_data_bytes"] = (total // len(sizes)) * len(rows) if sampled else total
            small = sum(1 for s in sizes if s < target_file_bytes)
            health["small_file_ratio"] = round(small / len(sizes), 4)
    except Exception as exc:  # noqa: BLE001 - metadata is best-effort
        logger.warning("iceberg_metadata aggregate failed for %s.%s: %s", schema, table, exc)

    if include_orphans and live_paths:
        health.update(_orphan_estimate(conn, live_paths, manifests, health.get("avg_file_bytes")))
    return health


def _measure_ducklake_table(
    conn: duckdb.DuckDBPyConnection, target: dict[str, Any]
) -> dict[str, Any] | None:
    """File count and bytes for one table, for the before/after of an apply.

    Returns None on failure: a failed measurement must not fail the maintenance.
    """
    catalog, schema, table = target.get("catalog"), target.get("schema"), target.get("table")
    if not (catalog and schema and table):
        return None
    try:
        row = conn.execute(
            "SELECT count(*)::BIGINT, coalesce(sum(data_file_size_bytes), 0)::BIGINT "
            "FROM ducklake_list_files(?, ?, schema => ?)",
            [catalog, table, schema],
        ).fetchone()
    except Exception as exc:  # noqa: BLE001 - measurement is best-effort
        logger.warning("Could not measure %s.%s for maintenance: %s", schema, table, exc)
        return None
    if row is None:
        return None
    return {"data_file_count": int(row[0]), "total_data_bytes": int(row[1])}


def collect_ducklake_table_health(
    conn: duckdb.DuckDBPyConnection,
    catalog: str,
    schema: str,
    table: str,
    *,
    target_file_bytes: int,
    metadata_schema: str | None = None,
    include_orphans: bool = False,
) -> dict[str, Any]:
    """Best-effort health metrics for one table in an attached DuckLake catalog.

    Same keys as `collect_table_health`, so scoring stays format-neutral.
    Snapshot counts and age are catalog-scoped, matching
    `ducklake_expire_snapshots`. Timestamps are reduced to numbers in SQL
    because the agent image has no `pytz` to convert DATETIMETZ.
    """
    health: dict[str, Any] = {
        "catalog": catalog,
        "schema": schema,
        "table": table,
        "snapshot_count": None,
        "snapshot_id": None,
        "oldest_snapshot_age_days": None,
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
        row = conn.execute(
            "SELECT count(*)::BIGINT, "
            "max(date_diff('second', snapshot_time, now())) / 86400.0 "
            "FROM ducklake_snapshots(?)",
            [catalog],
        ).fetchone()
        if row and row[0]:
            health["snapshot_count"] = int(row[0])
            if row[1] is not None:
                health["oldest_snapshot_age_days"] = round(float(row[1]), 4)
    except Exception as exc:  # noqa: BLE001 - metadata is best-effort
        logger.warning("ducklake_snapshots failed for %s.%s: %s", schema, table, exc)

    if metadata_schema:
        health["snapshot_id"] = _ducklake_table_snapshot(
            conn, catalog, schema, table, metadata_schema=metadata_schema
        )

    try:
        row = conn.execute(
            "SELECT count(*)::BIGINT, "
            "sum(data_file_size_bytes)::BIGINT, "
            "avg(data_file_size_bytes)::BIGINT, "
            "count(*) FILTER (WHERE data_file_size_bytes < ?)::BIGINT "
            "FROM ducklake_list_files(?, ?, schema => ?)",
            [target_file_bytes, catalog, table, schema],
        ).fetchone()
        if row:
            count = int(row[0])
            health["data_file_count"] = count
            if count:
                health["total_data_bytes"] = int(row[1]) if row[1] is not None else None
                health["avg_file_bytes"] = int(row[2]) if row[2] is not None else None
                health["small_file_ratio"] = round(int(row[3]) / count, 4)
    except Exception as exc:  # noqa: BLE001 - metadata is best-effort
        logger.warning("ducklake_list_files failed for %s.%s: %s", schema, table, exc)

    if include_orphans and metadata_schema:
        health.update(
            _ducklake_orphans(
                conn,
                catalog,
                schema,
                table,
                metadata_schema=metadata_schema,
                avg_file_bytes=health.get("avg_file_bytes"),
            )
        )
    return health


def _ducklake_table_snapshot(
    conn: duckdb.DuckDBPyConnection,
    catalog: str,
    schema: str,
    table: str,
    *,
    metadata_schema: str,
) -> int | None:
    """The newest catalog snapshot in which this table changed.

    Mirrors the control plane's `list_snapshots`; the cross-component suite
    keeps them in agreement.
    """
    meta = f'"__ducklake_metadata_{catalog}"."{metadata_schema}"'
    joins = (
        f"JOIN {meta}.ducklake_table t ON t.table_id = d.table_id "
        f"JOIN {meta}.ducklake_schema s ON s.schema_id = t.schema_id "
        "WHERE s.schema_name = ? AND t.table_name = ?"
    )
    table_join = (
        f"JOIN {meta}.ducklake_schema s ON s.schema_id = t.schema_id "
        "WHERE s.schema_name = ? AND t.table_name = ?"
    )
    sql = (
        "SELECT max(sid)::BIGINT FROM ("
        f"  SELECT t.begin_snapshot AS sid FROM {meta}.ducklake_table t {table_join}"
        f"  UNION ALL SELECT t.end_snapshot FROM {meta}.ducklake_table t {table_join}"
        f"  UNION ALL SELECT d.begin_snapshot FROM {meta}.ducklake_data_file d {joins}"
        f"  UNION ALL SELECT d.end_snapshot FROM {meta}.ducklake_data_file d {joins}"
        f"  UNION ALL SELECT d.begin_snapshot FROM {meta}.ducklake_delete_file d {joins}"
        f"  UNION ALL SELECT d.end_snapshot FROM {meta}.ducklake_delete_file d {joins}"
        ")"
    )
    try:
        row = conn.execute(sql, [schema, table] * 6).fetchone()
        return int(row[0]) if row and row[0] is not None else None
    except Exception as exc:  # noqa: BLE001 - metadata is best-effort
        logger.warning("ducklake snapshot lookup failed for %s.%s: %s", schema, table, exc)
        return None


def _ducklake_orphans(
    conn: duckdb.DuckDBPyConnection,
    catalog: str,
    schema: str,
    table: str,
    *,
    metadata_schema: str,
    avg_file_bytes: int | None,
) -> dict[str, Any]:
    """Files this table has superseded and DuckLake has scheduled for deletion.

    The count is exact, unlike Iceberg's glob-diff; bytes use the live average.
    """
    out: dict[str, Any] = {"orphan_file_count": None, "orphan_bytes": None}
    meta = f'"__ducklake_metadata_{catalog}"."{metadata_schema}"'
    try:
        row = conn.execute(
            f"SELECT count(*)::BIGINT FROM {meta}.ducklake_files_scheduled_for_deletion f "
            f"JOIN {meta}.ducklake_data_file d ON d.data_file_id = f.data_file_id "
            f"JOIN {meta}.ducklake_table t ON t.table_id = d.table_id "
            f"JOIN {meta}.ducklake_schema s ON s.schema_id = t.schema_id "
            "WHERE s.schema_name = ? AND t.table_name = ?",
            [schema, table],
        ).fetchone()
        if row is not None:
            count = int(row[0])
            out["orphan_file_count"] = count
            if avg_file_bytes is not None:
                out["orphan_bytes"] = count * int(avg_file_bytes)
    except Exception as exc:  # noqa: BLE001 - orphan detail is best-effort
        logger.warning("ducklake orphan scan failed for %s.%s: %s", schema, table, exc)
    return out


# Bounds per-file footer reads on the deep tier; the distribution is well
# estimated from a sample.
_MAX_FOOTER_READS = 1000


def _parquet_file_sizes(
    conn: duckdb.DuckDBPyConnection, paths: list[str]
) -> tuple[list[int], bool]:
    """Per-file sizes from the Parquet footers, for extension versions with no
    size column. One ranged read per file, hence deep tier only.

    Returns ``(sizes, sampled)``; a sampled subset is evenly spaced, so the
    caller can scale the total.
    """
    sampled = len(paths) > _MAX_FOOTER_READS
    if sampled:
        step = len(paths) / _MAX_FOOTER_READS
        paths = [paths[int(i * step)] for i in range(_MAX_FOOTER_READS)]
    try:
        rows = conn.execute(
            "SELECT file_name, sum(total_compressed_size) AS sz "
            "FROM parquet_metadata($files) GROUP BY file_name",
            {"files": paths},
        ).fetchall()
    except Exception as exc:  # noqa: BLE001 - the size probe is best-effort
        logger.warning("parquet_metadata size probe failed: %s", exc)
        return [], False
    return [int(sz) for _, sz in rows if sz is not None], sampled


def _orphan_estimate(
    conn: duckdb.DuckDBPyConnection,
    live_paths: list[str],
    manifest_paths: set[str],
    avg_file_bytes: int | None,
) -> dict[str, Any]:
    """Count files under the table location not referenced by current metadata.

    Derives the prefix from a live path, globs data and metadata, subtracts the
    live sets. No sizes from glob, so orphan bytes use the live average.
    """
    out: dict[str, Any] = {"orphan_file_count": None, "orphan_bytes": None}
    sample = live_paths[0]
    marker = "/data/"
    if marker not in sample:
        return out
    data_dir = sample[: sample.index(marker) + len(marker)]
    metadata_dir = data_dir.replace("/data/", "/metadata/")
    live = set(live_paths) | manifest_paths
    orphans: set[str] = set()
    for directory in (data_dir, metadata_dir):
        pattern = f"{directory}**".replace("'", "''")
        try:
            listed = {r[0] for r in conn.execute(f"SELECT file FROM glob('{pattern}')").fetchall()}
        except Exception as exc:  # noqa: BLE001 - listing is best-effort
            logger.warning("glob orphan scan failed for %s: %s", directory, exc)
            continue
        orphans |= listed - live
    out["orphan_file_count"] = len(orphans)
    if avg_file_bytes:
        out["orphan_bytes"] = len(orphans) * avg_file_bytes
    return out


def _configure_ducklake(conn: duckdb.DuckDBPyConnection) -> None:
    """Pin DuckLake's conflict-retry settings to the extension defaults.

    Deliberately not in `_ALLOWED_CONFIGS`: a statement must not change how its
    own writes retry.
    """
    for name, value in (
        ("ducklake_max_retry_count", "10"),
        ("ducklake_retry_backoff", "1.5"),
        ("ducklake_retry_wait_ms", "100"),
    ):
        try:
            conn.execute(f"SET {name} = {value}")
        except Exception as exc:  # noqa: BLE001 - an older extension may not know it
            logger.warning("Could not set %s: %s", name, exc)


def _attach_ducklake(conn: duckdb.DuckDBPyConnection, cat: dict[str, Any]) -> None:
    """ATTACH one DuckLake catalog, with the credentials the API vended for it.

    The Postgres password goes in a secret, not the DSN, because DuckLake
    echoes a failed attach's connection string.
    """
    slug = cat["slug"]
    alias = slug.replace('"', '""')
    meta = cat.get("meta") or {}
    store = cat.get("storage") or {}

    conn.execute(
        f"CREATE OR REPLACE SECRET {_meta_secret(slug)} "
        "(TYPE POSTGRES, HOST ?, PORT ?, DATABASE ?, USER ?, PASSWORD ?)",
        [meta["host"], int(meta["port"]), meta["database"], meta["user"], meta["password"]],
    )
    if store:
        _create_storage_secret(conn, slug, store)

    # ATTACH takes no bind parameters. Keep the escaping: `data_path` derives
    # from an operator-supplied `root_uri`.
    data_path = str(cat["data_path"]).replace("'", "''")
    metadata_schema = str(cat["metadata_schema"]).replace("'", "''")
    database = str(meta["database"]).replace("'", "''")
    host = str(meta["host"]).replace("'", "''")
    port = int(meta["port"])
    dsn = f"ducklake:postgres:dbname={database} host={host} port={port}"
    conn.execute(
        f"ATTACH '{dsn}' AS \"{alias}\" ("
        f"DATA_PATH '{data_path}', METADATA_SCHEMA '{metadata_schema}', "
        f"META_SECRET '{_meta_secret(slug)}', CREATE_IF_NOT_EXISTS true)"
    )
    _apply_ducklake_options(conn, alias, cat.get("options") or {})

    # Created here, not at provisioning, because it needs the extension.
    schema = (cat.get("default_schema") or _DEFAULT_NAMESPACE).replace('"', '""')
    conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{alias}"."{schema}"')


_SIZE_UNITS = {
    "kb": 1000,
    "mb": 1000**2,
    "gb": 1000**3,
    "tb": 1000**4,
    "kib": 1024,
    "mib": 1024**2,
    "gib": 1024**3,
    "tib": 1024**4,
}
_SIZE_VALUE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([kmgt]i?b)\s*$", re.IGNORECASE)


def _normalise_option_value(value: object) -> str:
    """An option value in the form DuckLake stores it.

    `set_option` takes sizes with a unit ("512MB") but stores bytes
    ("512000000"), using DuckDB's rule: KB-TB are powers of 1000, KiB-TiB 1024.
    """
    text = str(value).strip()
    if match := _SIZE_VALUE.match(text):
        return str(int(float(match.group(1)) * _SIZE_UNITS[match.group(2).lower()]))
    return text


def _current_ducklake_options(conn: duckdb.DuckDBPyConnection, alias: str) -> dict[str, str]:
    rows = conn.execute(
        "SELECT option_name, value FROM ducklake_options(?) WHERE scope = 'GLOBAL'", [alias]
    ).fetchall()
    return {name: _normalise_option_value(value) for name, value in rows}


def _apply_ducklake_options(
    conn: duckdb.DuckDBPyConnection, alias: str, options: dict[str, Any]
) -> None:
    """Apply the catalog options the API vended, writing only those that differ.

    `set_option` updates `ducklake_metadata` even when the value is unchanged,
    and every attach runs this. Concurrent attaches then collide on the same
    rows ("could not serialize access due to concurrent update"), so the
    current values are read first and the steady state writes nothing.
    """
    if not options:
        return
    try:
        current: dict[str, str] | None = _current_ducklake_options(conn, alias)
    except Exception as exc:  # noqa: BLE001 - an older extension may lack the function
        logger.debug("Could not read DuckLake options on %s: %s", alias, exc)
        current = None

    for name, value in options.items():
        wanted = _normalise_option_value(value)
        if current is not None and current.get(name) == wanted:
            continue
        try:
            conn.execute(f'CALL "{alias}".set_option(?, ?)', [name, str(value)])
        except Exception as exc:  # noqa: BLE001 - an older extension may not know it
            if _option_now_matches(conn, alias, name, wanted):
                # Another attach wrote the same value first.
                logger.debug("DuckLake option %s on %s was set concurrently", name, alias)
                continue
            logger.warning("Could not set DuckLake option %s on %s: %s", name, alias, exc)


def _option_now_matches(
    conn: duckdb.DuckDBPyConnection, alias: str, name: str, wanted: str
) -> bool:
    try:
        return _current_ducklake_options(conn, alias).get(name) == wanted
    except Exception:  # noqa: BLE001 - nothing to compare against
        return False


def _create_storage_secret(
    conn: duckdb.DuckDBPyConnection, slug: str, store: dict[str, Any]
) -> None:
    """The object-store secret for a DuckLake catalog, SCOPEd to its own prefix
    so it cannot serve another catalog attached to the same connection.
    """
    name = _storage_secret(slug)
    if store.get("type") == "azure":
        conn.execute(
            f"CREATE OR REPLACE SECRET {name} "
            "(TYPE AZURE, PROVIDER config, CONNECTION_STRING ?, ACCOUNT_NAME ?)",
            [store["connection_string"], store["account_name"]],
        )
        return
    conn.execute(
        f"CREATE OR REPLACE SECRET {name} (TYPE S3, PROVIDER config, KEY_ID ?, SECRET ?, "
        "SESSION_TOKEN ?, REGION ?, ENDPOINT ?, URL_STYLE ?, USE_SSL ?, SCOPE ?)",
        [
            store.get("key_id", ""),
            store.get("secret", ""),
            store.get("session_token", ""),
            store.get("region", ""),
            store.get("endpoint", ""),
            store.get("url_style", "path"),
            bool(store.get("use_ssl", False)),
            store.get("scope", ""),
        ],
    )


def _attach_catalogs(
    conn: duckdb.DuckDBPyConnection,
    *,
    catalogs: list[dict[str, Any]],
    active_catalog: str | None,
    polaris: dict[str, Any],
    trace_headers: dict[str, str] | None = None,
) -> None:
    """ATTACH every catalog bound to the workspace (multi-attach), by kind.

    Each catalog is attached under its slug alias so SQL can address
    `catalog.schema.table` and join across catalogs and kinds. The active
    catalog is then `USE`d. Per-catalog ATTACH is best-effort: one bad catalog
    is logged and skipped rather than failing the whole query.
    """
    # A DuckLake-only workspace gets no Polaris block.
    has_iceberg = any(c.get("kind", "iceberg_polaris") != KIND_DUCKLAKE for c in catalogs)
    endpoint = str(polaris.get("endpoint", "")).rstrip("/")
    # `trace_headers` carries the caller's span onto every DuckDB-issued Polaris
    # request, so Polaris's spans join this query's trace. It must be captured on
    # the event-loop thread by the caller: this runs in a worker thread, where
    # OpenTelemetry's contextvar "current span" is not propagated.
    if trace_headers and endpoint:
        conn.execute(
            f"CREATE OR REPLACE SECRET {_TRACE_HEADERS_SECRET} "
            "(TYPE HTTP, EXTRA_HTTP_HEADERS ?, SCOPE ?)",
            [trace_headers, endpoint],
        )
    if has_iceberg:
        conn.execute(
            f"CREATE SECRET {_ICEBERG_SECRET} "
            "(TYPE ICEBERG, CLIENT_ID ?, CLIENT_SECRET ?, OAUTH2_SERVER_URI ?)",
            [
                polaris["client_id"],
                polaris["client_secret"],
                f"{endpoint}/api/catalog/v1/oauth/tokens",
            ],
        )
    # ATTACH takes no bind parameters; inline as quoted, escaped literals.
    cat_endpoint = f"{endpoint}/api/catalog".replace("'", "''")
    active = None
    for cat in catalogs:
        slug = cat["slug"]
        backend_kind = (cat.get("backend") or {}).get("kind")
        try:
            if cat.get("kind") == KIND_DUCKLAKE:
                _attach_ducklake(conn, cat)
            else:
                delegation = "vended_credentials" if backend_kind in _VENDED_BACKENDS else "none"
                wh = str(cat["polaris_name"]).replace("'", "''")
                alias = slug.replace('"', '""')
                # PURGE_REQUESTED: the catalog's drop-with-purge flag only allows a
                # purge, and DuckDB otherwise drops with purgeRequested=false, which
                # leaves every data and metadata file on object storage.
                conn.execute(
                    f"ATTACH '{wh}' AS \"{alias}\" "
                    f"(TYPE ICEBERG, SECRET {_ICEBERG_SECRET}, ENDPOINT '{cat_endpoint}', "
                    f"ACCESS_DELEGATION_MODE '{delegation}', PURGE_REQUESTED true)"
                )
        except Exception as exc:  # noqa: BLE001 - one bad catalog must not fail the query
            logger.warning("ATTACH failed for catalog %s: %s", slug, exc)
            continue
        if slug == active_catalog:
            active = cat
    # `USE <catalog>.<schema>` sets both defaults; a bare `USE <catalog>` does
    # not reliably resolve an attached Iceberg catalog.
    if active is None and catalogs:
        active = catalogs[0]
    if active is not None:
        schema = (active.get("default_schema") or _DEFAULT_NAMESPACE).replace('"', '""')
        aslug = active["slug"].replace('"', '""')
        conn.execute(f'USE "{aslug}"."{schema}"')


# Filesystem names DuckDB actually registers (core + the httpfs/azure extensions
# the agent loads). Verified on 1.5.4: `SET disabled_filesystems` accepts ANY
# string without error, so a typo silently disables nothing — exactly the
# "comment that looks like a defense" failure this sandbox exists to avoid. We
# therefore validate against this list and warn on anything unrecognized.
_KNOWN_FILESYSTEMS = frozenset(
    {
        "LocalFileSystem",
        "HTTPFileSystem",
        "S3FileSystem",
        "GCSFileSystem",
        "AzureBlobStorageFileSystem",
        "AzureDfsStorageFileSystem",
        "HuggingFaceFileSystem",
    }
)

# Settings that must remain writable after `lock_configuration` — DuckDB's
# `allowed_configs` exception list. Each entry is a legitimate need of code that
# runs AFTER the sandbox is applied:
#   memory_limit / threads              -> _run_one_statement, per statement
#   enable_profiling / profiling_output /
#     custom_profiling_settings         -> _run_one_statement's profile capture
#   TimeZone                            -> the `SET timezone` the API statement
#                                          policy deliberately admits
# Everything else — disabled_filesystems, enable_external_access,
# secret_directory, extension_directory, home_directory, custom_extension_repository,
# allow_unsigned_extensions, and `allowed_configs`/`lock_configuration` themselves
# — becomes un-widenable for the life of the connection. `SET search_path`/`SET
# schema`/`USE` are unaffected: they are not configuration options.
_ALLOWED_CONFIGS = (
    "memory_limit",
    "threads",
    "TimeZone",
    "enable_profiling",
    "profiling_output",
    "custom_profiling_settings",
)


# Wording DuckDB uses when one of the sandbox's own guards refuses a statement:
# a disabled filesystem, or a `SET` refused because the configuration is locked.
# Matched on the message because DuckDB reuses the same exception types for
# ordinary user errors (see `_is_sandbox_denial`).
_SANDBOX_DENIAL_MARKERS = (
    "has been disabled by configuration",
    "the configuration has been locked",
    "file system operations are disabled by configuration",
)


def _is_sandbox_denial(exc: Exception) -> bool:
    """True when ``exc`` is the sandbox refusing a statement, not a user error."""
    msg = str(exc).lower()
    return any(marker in msg for marker in _SANDBOX_DENIAL_MARKERS)


def _apply_sandbox(
    conn: duckdb.DuckDBPyConnection,
    disabled_filesystems: str | None,
    *,
    lock_config: bool,
) -> None:
    """Apply the DuckDB sandbox (defense-in-depth beneath the API statement policy).

    Two independent controls, both verified on DuckDB 1.5.4:

    - ``disabled_filesystems`` — a one-way latch. Once a filesystem is disabled,
      neither ``SET disabled_filesystems=''`` nor ``RESET`` can restore it
      ("has been disabled previously, it cannot be re-enabled"). Empty/None is a
      no-op; see ``agent.config.Settings.sandbox_disabled_filesystems`` for why it
      ships off.
    - ``lock_config`` — ``allowed_configs`` + ``lock_configuration``, so a session
      statement cannot re-widen the sandbox with ``SET``. Applied last, after the
      IO extensions are loaded, the catalogs are attached, and the secrets exist,
      so only subsequent user statements are constrained.

    Best-effort throughout: a DuckDB error is logged rather than raised, so an
    unexpected engine change degrades the sandbox instead of failing every query.
    """
    if disabled_filesystems and disabled_filesystems.strip():
        names = disabled_filesystems.replace(",", " ").split()
        known = [n for n in names if n in _KNOWN_FILESYSTEMS]
        for unknown in (n for n in names if n not in _KNOWN_FILESYSTEMS):
            logger.warning(
                "Ignoring unknown filesystem %r in sandbox_disabled_filesystems "
                "(DuckDB accepts unknown names silently, so this would disable nothing); "
                "known names: %s",
                unknown,
                ", ".join(sorted(_KNOWN_FILESYSTEMS)),
            )
        if known:
            escaped = ",".join(n.replace("'", "''") for n in known)
            try:
                conn.execute(f"SET disabled_filesystems='{escaped}'")
            except duckdb.Error as exc:
                logger.warning("Could not apply disabled_filesystems sandbox: %s", exc)

    if lock_config:
        allowed = ",".join(f"'{name}'" for name in _ALLOWED_CONFIGS)
        try:
            conn.execute(f"SET allowed_configs=[{allowed}]")
            conn.execute("SET lock_configuration=true")
        except duckdb.Error as exc:
            logger.warning("Could not lock DuckDB configuration: %s", exc)


def open_and_attach(
    *,
    catalogs: list[dict[str, Any]] | None = None,
    active_catalog: str | None = None,
    polaris: dict[str, Any] | None = None,
    trace_headers: dict[str, str] | None = None,
    disabled_filesystems: str | None = None,
    lock_config: bool = False,
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
            if _safe_install_load(conn, io_ext) and io_ext == "httpfs":
                # Only meaningful once httpfs is loaded; it owns the setting.
                _enable_connection_caching(conn)
    # External cloud backends (s3/adls_gen2) talk HTTPS and need a CA bundle the
    # statically-linked extensions can't find on their own.
    if backend_kinds - {None, "object_store"}:
        _configure_external_tls(conn, azure="adls_gen2" in backend_kinds)

    catalog_kinds = {cat.get("kind", "iceberg_polaris") for cat in catalogs}
    loaded: set[str] = set()
    for catalog_kind in catalog_kinds:
        for ext in _CATALOG_KIND_EXTENSIONS.get(catalog_kind, ()):
            if ext not in loaded and _safe_install_load(conn, ext):
                loaded.add(ext)
    # Drop catalogs whose extensions failed to load: the iceberg secret is
    # created outside the per-catalog guard, so a failed `iceberg` load would
    # otherwise take the DuckLake attaches down with it.
    catalogs = [
        cat
        for cat in catalogs
        if all(
            ext in loaded
            for ext in _CATALOG_KIND_EXTENSIONS.get(cat.get("kind", "iceberg_polaris"), ())
        )
    ]
    if KIND_DUCKLAKE in catalog_kinds:
        _configure_ducklake(conn)

    if catalogs and (polaris or KIND_DUCKLAKE in catalog_kinds):
        try:
            _attach_catalogs(
                conn,
                catalogs=catalogs,
                active_catalog=active_catalog,
                polaris=polaris or {},
                trace_headers=trace_headers,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Catalog ATTACH failed: %s", exc)
    # Apply the sandbox last: the IO extensions are loaded and catalogs are
    # attached, so disabling a filesystem (and locking the configuration) here
    # only constrains subsequent user-statement access, not the trusted
    # attach/credential-vending path.
    _apply_sandbox(conn, disabled_filesystems, lock_config=lock_config)
    return conn


def apply_memory_limit(conn: duckdb.DuckDBPyConnection, memory_bytes: int) -> None:
    """Move a connection's DuckDB memory limit to ``memory_bytes``.

    GiB, not GB: the value is bytes/1024**3, and DuckDB reads a `GB` suffix as
    10**9. Labelling it GB handed DuckDB ~7% less than the admission manager
    granted, so the real headroom was ~17% against a configured 10% and every
    slot silently lost ~250 MiB of its slice. This is the one place that
    spelling lives — every caller that resizes a connection goes through here.

    Note the limit bounds more than operator memory: it also caps DuckDB's
    ``EXTERNAL_FILE_CACHE``, which is what keeps an Iceberg scan from re-reading
    its Parquet from object storage. Lowering it evicts that cache (measured on
    1.5.5: 383 MB -> 64 MB in 12 ms), which is exactly what makes the admission
    manager's elastic tier revocable — and also why nothing should lower it
    casually.
    """
    conn.execute(f"SET memory_limit='{memory_bytes / 1024**3}GiB'")


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


def _result_schema(rel: duckdb.DuckDBPyRelation) -> list[dict[str, str]] | None:
    """``[{"name", "type"}, ...]`` for a materialized result, or ``None``.

    ``type`` is DuckDB's own logical-type spelling — the same string ``DESCRIBE``
    prints in its ``column_type`` column, and the repo's established
    column-metadata contract. It is fully self-describing: nested and
    parameterized types (``DECIMAL(38,10)``, ``STRUCT(a INTEGER, b VARCHAR)``,
    ``ENUM('e', 'f')``, ``INTEGER[2]``) re-parse to themselves, so no separate
    precision/scale fields are needed.

    Best-effort (mirrors ``_capture_profile``): any failure returns ``None``, so
    a client sees no schema rather than a wrong one.
    """
    try:
        return [{"name": n, "type": str(t)} for n, t in zip(rel.columns, rel.types, strict=True)]
    except Exception as exc:  # noqa: BLE001 - schema capture is best-effort
        logger.warning("Result schema capture failed: %s", exc)
        return None


def _apply_watermarks(summary: dict[str, Any], watermarks: dict[str, int]) -> None:
    """Turn connection-lifetime high-water marks into per-statement numbers.

    DuckDB's ``system_peak_buffer_memory`` and ``system_peak_temp_dir_size`` are
    high-water marks for the whole connection, not for one statement. Verified on
    1.5.5: after a heavy group-by reports 475 MB, a plain ``SELECT 1`` on the same
    connection reports 475 MB too, and so does the next one. A held session reuses
    one connection for its whole life, so every statement after the first was
    reporting the session's running maximum rather than its own usage — wrong
    numbers in the profile UI and in anything else reading them.

    There is no pragma that resets these counters, so subtract what earlier
    statements on this connection already accounted for. A fresh connection (the
    one-shot query path) starts at zero, so the delta equals the raw value and
    that path is unchanged.
    """
    for key in ("peak_memory_bytes", "spill_bytes"):
        raw = int(summary.get(key) or 0)
        previous = watermarks.get(key, 0)
        summary[key] = max(0, raw - previous)
        watermarks[key] = max(previous, raw)


def _run_one_statement(
    conn: duckdb.DuckDBPyConnection,
    sql: str,
    result_path: Path,
    *,
    memory_bytes: int,
    threads: int,
    enable_profiling: bool,
    watermarks: dict[str, int] | None = None,
    admission_wait_ms: float = 0.0,
) -> dict[str, Any]:
    """Set the connection's resource slice and run one statement.

    A single `SELECT` is materialized to Parquet (`wrote_result=True`, profiled);
    any other statement — DDL/DML or a multi-statement script — is executed
    directly with no result file. Returns the base result dict
    (`row_count`, `duration_ms`, `wrote_result`, `result_bytes`, `profile`,
    `result_schema`).

    Shared by the per-query path (`run_query_sync`) and the per-session path
    (`run_statement_sync`); it owns the profile sidecar file but NOT the
    connection lifecycle (the caller opens/closes/holds the connection).
    """
    # The admission manager sizes each query's slice of the agent's budget
    # (memory_bytes + threads) so concurrent sessions never oversubscribe the
    # cgroup memory limit. DuckDB's default thread count ignores the cgroup CPU
    # quota, so `threads` is set explicitly. (For a held session `memory_bytes`
    # is the reservation's required floor plus whatever revocable cache the
    # admission manager could spare, so it moves from statement to statement.)
    apply_memory_limit(conn, memory_bytes)
    conn.execute(f"SET threads={threads}")
    # Sibling of the result file; retention only sweeps `*.parquet`, so we own
    # this file's lifecycle and unlink it ourselves.
    profile_path = result_path.with_suffix(".profile.json")

    start = time.monotonic()
    wrote_result = _is_single_select(sql)
    result_bytes: int | None = None
    profile: dict[str, Any] | None = None
    result_schema: list[dict[str, str]] | None = None
    try:
        if wrote_result:
            # A single SELECT is materialized to Parquet so the control plane can
            # page through its rows. Profile only this path so DDL/DML carry no
            # profile (the UI shows a no-profile state for them).
            if enable_profiling:
                conn.execute("PRAGMA enable_profiling='json'")
                conn.execute(f"PRAGMA profiling_output='{profile_path}'")
                conn.execute(f"PRAGMA custom_profiling_settings='{_PROFILE_SETTINGS_JSON}'")
            # Materialize through the relational API rather than a string-built
            # `COPY ({sql}) TO …`. `COPY`'s source may only be a table name or a
            # query, so every other shape `_is_single_select` admits — `DESCRIBE`,
            # `SHOW`, `SUMMARIZE`, `PRAGMA` — is a parser error there. Wrapping the
            # body in `SELECT * FROM (…)` would rescue all but `PRAGMA`, leaving the
            # same trap set. `conn.sql()` builds a lazy relation and `write_parquet`
            # streams it through the identical `BATCH_COPY_TO_FILE` operator, so the
            # Parquet output and the captured profile are unchanged for a SELECT.
            rel = conn.sql(sql)
            rel.write_parquet(str(result_path))
            duration_ms = int((time.monotonic() - start) * 1000)
            # The relation's own types, read before the Parquet hop: the writer
            # is lossy (HUGEINT -> DOUBLE, ENUM -> VARCHAR, INTEGER[2] -> INTEGER[],
            # BIT -> VARCHAR), so the control plane cannot recover these from the
            # file it proxies. This is the only place the query's real result
            # types exist.
            result_schema = _result_schema(rel)
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
                profile["summary"]["admission_wait_ms"] = admission_wait_ms
                if watermarks is not None:
                    _apply_watermarks(profile["summary"], watermarks)
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
    except duckdb.Error as exc:
        # A defeated escape attempt is worth a WARNING operators can alert on, but
        # only when it really is one: DuckDB raises InvalidInputException for
        # plenty of ordinary user mistakes, so match the sandbox's own wording
        # rather than the exception type alone.
        if _is_sandbox_denial(exc):
            logger.warning("Statement blocked by the DuckDB sandbox: %s", exc)
        raise
    finally:
        profile_path.unlink(missing_ok=True)
    return {
        "row_count": row_count,
        "duration_ms": duration_ms,
        "wrote_result": wrote_result,
        "result_bytes": result_bytes,
        "profile": profile,
        "result_schema": result_schema,
    }


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
    maintain_for: dict[str, Any] | None = None,
    conn: duckdb.DuckDBPyConnection | None = None,
    enable_profiling: bool = True,
    on_connect: Callable[[duckdb.DuckDBPyConnection], None] | None = None,
    trace_headers: dict[str, str] | None = None,
    disabled_filesystems: str | None = None,
    lock_config: bool = False,
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
            disabled_filesystems=disabled_filesystems,
            lock_config=lock_config,
        )
        if on_connect is not None:
            on_connect(c)
        return c

    if conn is None:
        conn = _open_fresh()
    elif on_connect is not None:
        on_connect(conn)
    # We can transparently re-vend credentials only when we know how to re-ATTACH.
    can_reattach = bool(catalogs and polaris)

    def _execute() -> dict[str, Any]:
        # Measured on this connection so the delta reflects this statement alone.
        before = _measure_ducklake_table(conn, maintain_for) if maintain_for else None

        result = _run_one_statement(
            conn,
            sql,
            result_path,
            memory_bytes=memory_bytes,
            threads=threads,
            enable_profiling=enable_profiling,
        )

        if maintain_for:
            result["maintenance"] = {
                "before": before,
                "after": _measure_ducklake_table(conn, maintain_for),
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
                # Table-format-native metadata for the table-detail page.
                if catalogs:
                    if _catalog_kind(catalogs, catalog) == KIND_DUCKLAKE:
                        result["ducklake"] = _ducklake_metadata(conn, catalog, schema, table)
                    elif polaris:
                        result["iceberg"] = _iceberg_metadata(conn, catalog, schema, table)

        # Maintenance health probe, driven by the scanner. Not gated on Polaris:
        # a DuckLake catalog has none.
        if health_for and catalogs:
            catalog = health_for.get("catalog")
            schema = health_for.get("schema")
            table = health_for.get("table")
            if catalog and schema and table:
                is_ducklake = _catalog_kind(catalogs, catalog) == KIND_DUCKLAKE
                try:
                    if is_ducklake:
                        result["health"] = collect_ducklake_table_health(
                            conn,
                            catalog,
                            schema,
                            table,
                            target_file_bytes=int(
                                health_for.get("target_file_bytes", 128 * 1024**2)
                            ),
                            metadata_schema=_catalog_metadata_schema(catalogs, catalog),
                            include_orphans=bool(health_for.get("include_orphans", False)),
                        )
                    elif polaris:
                        result["health"] = collect_table_health(
                            conn,
                            catalog,
                            schema,
                            table,
                            target_file_bytes=int(
                                health_for.get("target_file_bytes", 128 * 1024**2)
                            ),
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


def run_statement_sync(
    sql: str,
    result_path: Path,
    *,
    conn: duckdb.DuckDBPyConnection,
    memory_bytes: int,
    threads: int,
    enable_profiling: bool = True,
    watermarks: dict[str, int] | None = None,
    admission_wait_ms: float = 0.0,
) -> dict[str, Any]:
    """Run one statement on a held SQL-session connection.

    Unlike `run_query_sync`, this runs against an already-open, already-attached
    connection and **does not close it** — the session owns the connection for its
    whole lifetime (`agent.control.session`). Reuses the same
    materialize-single-SELECT-to-Parquet path so the control plane pages statement
    results through the identical fetch pipeline as ordinary queries.

    `memory_bytes`/`threads` are what admission granted this statement — under
    `auto` the session grows to its estimate for the statement and shrinks back
    afterwards, so unlike a one-shot query these change from statement to
    statement.

    `watermarks` is the session's running record of DuckDB's connection-lifetime
    peak counters, which is what makes the reported peak/spill belong to this
    statement rather than to the heaviest one that ran before it. It is mutated
    in place; see `_apply_watermarks`.
    """
    return _run_one_statement(
        conn,
        sql,
        result_path,
        memory_bytes=memory_bytes,
        threads=threads,
        enable_profiling=enable_profiling,
        watermarks=watermarks,
        admission_wait_ms=admission_wait_ms,
    )
