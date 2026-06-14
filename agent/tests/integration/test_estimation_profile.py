"""Cost estimation + post-execution profiling through the real Iceberg path.

The unit tests cover the estimator/profiler against local DuckDB tables; these
exercise them against a *real* attached Polaris/MinIO catalog, so the EXPLAIN
estimate and the post-execution profile are produced over the same object-store
read path the agent uses in production (format drift or attach regressions
break this).
"""

from __future__ import annotations

import pytest

from agent.executor.estimator import bucket_for, estimate_memory_bytes
from agent.executor.runner import run_query_sync
from duckhaven_shared.concurrency import BUCKET_FRACTIONS

pytestmark = pytest.mark.integration


def _seed(conn, rows: int = 50000) -> None:
    conn.execute("CREATE TABLE prof_src (id BIGINT, g BIGINT, label VARCHAR)")
    conn.execute(
        "INSERT INTO prof_src SELECT i, i % 500, 'row-' || i FROM range(?) t(i)",
        [rows],
    )


async def test_estimate_over_real_catalog(polaris_s3_catalog, attach_factory) -> None:
    """EXPLAIN-based estimation binds against the attached catalog and sizes a
    blocking GROUP BY above a streaming filter."""
    catalog, ns = polaris_s3_catalog
    conn = attach_factory(catalog, ns)
    _seed(conn)

    heavy = estimate_memory_bytes(
        conn, "SELECT g, count(*) c FROM prof_src GROUP BY g ORDER BY c DESC", safety=1.5
    )
    streaming = estimate_memory_bytes(conn, "SELECT id FROM prof_src WHERE id > 5", safety=1.5)

    assert heavy is not None and heavy > 0
    assert streaming == 0  # no blocking operator
    # The heavy query buckets at least as large as the streaming one.
    budget = 4 * 1024**3
    _, heavy_frac, _ = bucket_for(heavy, budget, BUCKET_FRACTIONS)
    _, stream_frac, _ = bucket_for(streaming, budget, BUCKET_FRACTIONS)
    assert heavy_frac >= stream_frac


async def test_profile_captured_over_real_catalog(
    polaris_s3_catalog, attach_factory, tmp_path
) -> None:
    """A SELECT materialized from a real Iceberg table yields a normalized
    profile with query-level actuals and an operator tree whose scan reads the
    seeded rows."""
    catalog, ns = polaris_s3_catalog
    conn = attach_factory(catalog, ns)
    _seed(conn)

    result_path = tmp_path / "out.parquet"
    stats = run_query_sync(
        "SELECT g, count(*) c FROM prof_src GROUP BY g ORDER BY c DESC",
        result_path,
        memory_bytes=1024**3,
        threads=2,
        conn=conn,
        enable_profiling=True,
    )

    assert stats["row_count"] == 500  # 500 distinct groups
    profile = stats["profile"]
    assert profile is not None
    assert profile["summary"]["latency_ms"] > 0
    assert profile["summary"]["peak_memory_bytes"] >= 0

    # The operator tree includes a scan that read the seeded rows.
    scanned = []
    stack = [profile["tree"]]
    while stack:
        node = stack.pop()
        if node["rows_scanned"]:
            scanned.append(node["rows_scanned"])
        stack.extend(node["children"])
    assert 50000 in scanned


async def test_profile_temp_file_cleaned_up(polaris_s3_catalog, attach_factory, tmp_path) -> None:
    """The temp profile JSON is removed (retention only sweeps *.parquet)."""
    catalog, ns = polaris_s3_catalog
    conn = attach_factory(catalog, ns)
    _seed(conn, rows=100)
    result_path = tmp_path / "out.parquet"
    run_query_sync(
        "SELECT id FROM prof_src ORDER BY id",
        result_path,
        memory_bytes=1024**3,
        threads=1,
        conn=conn,
        enable_profiling=True,
    )
    assert not (tmp_path / "out.profile.json").exists()
