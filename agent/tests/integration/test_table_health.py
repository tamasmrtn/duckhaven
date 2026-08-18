"""Agent maintenance health probe against a live Polaris + object store.

Opt-in (`-m integration`); requires a live Polaris on object storage (see
conftest; `make polaris-dev` provides a local MinIO-backed stack). This is the
verification gate for the maintenance advisor's data source: it confirms that
``runner.collect_table_health`` resolves the version-sensitive
``iceberg_snapshots`` / ``iceberg_metadata`` / ``glob`` calls against the agent's
DuckDB ``iceberg`` extension over an attached REST catalog, and that the derived
file-size distribution and orphan estimate behave on real metadata.
"""

from __future__ import annotations

import duckdb
import pytest

from agent.executor import runner

pytestmark = pytest.mark.integration

# 128 MiB — the conventional Iceberg target; test rows are far smaller, so every
# data file should count as "small".
_TARGET_FILE_BYTES = 128 * 1024 * 1024


def _attach(
    conn: duckdb.DuckDBPyConnection,
    base_url: str,
    warehouse: str,
    ns: str,
    creds,
) -> None:
    runner._attach_catalogs(
        conn,
        catalogs=[
            {
                "slug": warehouse,
                "polaris_name": warehouse,
                "backend": {"kind": "s3"},
                "default_schema": ns,
            }
        ],
        active_catalog=warehouse,
        polaris={
            "endpoint": base_url,
            "client_id": creds[0],
            "client_secret": creds[1],
        },
    )


async def test_collect_table_health_on_written_table(
    polaris_base_url: str, polaris_creds, polaris_s3_catalog: tuple[str, str]
) -> None:
    """After several small INSERTs, the probe reports snapshot/file/manifest
    counts, and — when the iceberg extension exposes a file-size column — a
    file-size distribution that flags the tiny files as small."""
    catalog, ns = polaris_s3_catalog
    conn = duckdb.connect()
    try:
        conn.execute("INSTALL httpfs")
        conn.execute("LOAD httpfs")
        _attach(conn, polaris_base_url, catalog, ns, polaris_creds)
        # Each INSERT is its own commit -> multiple snapshots and data files.
        conn.execute("INSERT INTO events VALUES (1, 'one')")
        conn.execute("INSERT INTO events VALUES (2, 'two')")
        conn.execute("INSERT INTO events VALUES (3, 'three')")
        health = runner.collect_table_health(
            conn, catalog, ns, "events", target_file_bytes=_TARGET_FILE_BYTES
        )
    finally:
        conn.close()

    assert health["schema"] == ns
    assert health["table"] == "events"
    assert health["snapshot_count"] is not None and health["snapshot_count"] >= 1
    assert health["snapshot_id"] is not None
    assert (
        health["oldest_snapshot_age_days"] is not None and health["oldest_snapshot_age_days"] >= 0
    )
    assert health["data_file_count"] is not None and health["data_file_count"] >= 1
    assert health["manifest_count"] is not None and health["manifest_count"] >= 1
    # File sizes are only available when the iceberg extension exposes a size
    # column, which varies by version. When present they must be self-consistent
    # (every written file is far below the 128 MiB target); when absent the probe
    # degrades these fields to None rather than failing the scan.
    if health["total_data_bytes"] is not None:
        assert health["total_data_bytes"] > 0
        assert health["avg_file_bytes"] is not None and health["avg_file_bytes"] > 0
        assert health["small_file_ratio"] == 1.0
    else:
        assert health["avg_file_bytes"] is None
        assert health["small_file_ratio"] is None


async def test_collect_table_health_deep_tier_estimates_orphans(
    polaris_base_url: str, polaris_creds, polaris_s3_catalog: tuple[str, str]
) -> None:
    """The deep tier (``include_orphans``) lists the data and metadata directories
    for a non-negative orphan estimate, and derives file sizes from the Parquet
    footers when the iceberg extension exposes no size column."""
    catalog, ns = polaris_s3_catalog
    conn = duckdb.connect()
    try:
        conn.execute("INSTALL httpfs")
        conn.execute("LOAD httpfs")
        _attach(conn, polaris_base_url, catalog, ns, polaris_creds)
        conn.execute("INSERT INTO events VALUES (1, 'one'), (2, 'two')")
        health = runner.collect_table_health(
            conn,
            catalog,
            ns,
            "events",
            target_file_bytes=_TARGET_FILE_BYTES,
            include_orphans=True,
        )
    finally:
        conn.close()

    assert health["data_file_count"] is not None and health["data_file_count"] >= 1
    # Orphan detection ran (field populated) and never goes negative. A freshly
    # written table typically has zero orphans, but in-flight artifacts may show
    # a few — either way it is only an estimate, never a deletion.
    assert health["orphan_file_count"] is not None
    assert health["orphan_file_count"] >= 0
    # The deep tier resolves file sizes (Parquet-footer fallback when the iceberg
    # extension exposes no size column), so the size-derived metrics populate even
    # though the cheap tier left them None. The written files are tiny -> all small.
    assert health["total_data_bytes"] is not None and health["total_data_bytes"] > 0
    assert health["avg_file_bytes"] is not None and health["avg_file_bytes"] > 0
    assert health["small_file_ratio"] == 1.0
