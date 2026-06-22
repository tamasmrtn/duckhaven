import time

import pytest

from agent.executor.runner import run_query_sync
from agent.executor.supervisor import run_query

# The admission manager normally supplies per-query sizing; tests that don't
# exercise sizing pass a fixed slice via this helper.
_MEM = 1024**3
_THREADS = 2


def _run(sql, result_path, **kwargs):
    kwargs.setdefault("memory_bytes", _MEM)
    kwargs.setdefault("threads", _THREADS)
    return run_query_sync(sql, result_path, **kwargs)


def test_simple_select_produces_parquet(tmp_path):
    result_path = tmp_path / "out.parquet"
    stats = _run("SELECT 42 AS answer", result_path)
    assert result_path.exists()
    assert stats["row_count"] == 1
    assert stats["wrote_result"] is True
    assert stats["duration_ms"] >= 0


def test_select_reports_result_bytes(tmp_path):
    """A materialized SELECT reports the Parquet result file's size."""
    result_path = tmp_path / "out.parquet"
    stats = _run("SELECT * FROM range(100) t(x)", result_path)
    assert stats["result_bytes"] == result_path.stat().st_size
    assert stats["result_bytes"] > 0


def test_ddl_runs_without_result_file(tmp_path):
    """Pure DDL executes but writes no Parquet and reports zero rows."""
    result_path = tmp_path / "out.parquet"
    stats = _run("CREATE TABLE t (x INT)", result_path)
    assert not result_path.exists()
    assert stats["wrote_result"] is False
    assert stats["row_count"] == 0
    assert stats["result_bytes"] is None


def test_dml_reports_affected_count(tmp_path):
    """A multi-statement DDL+DML script runs directly and reports the affected
    row count from the final statement (no result file)."""
    result_path = tmp_path / "out.parquet"
    stats = _run(
        "CREATE TABLE t (x INT); INSERT INTO t VALUES (1), (2), (3)",
        result_path,
    )
    assert not result_path.exists()
    assert stats["wrote_result"] is False
    assert stats["row_count"] == 3


def test_multiple_rows(tmp_path):
    result_path = tmp_path / "out.parquet"
    stats = _run("SELECT unnest([1,2,3]) AS n", result_path)
    assert stats["row_count"] == 3


@pytest.mark.parametrize(("mem_bytes", "threads"), [(4 * 1024**3, 3), (16 * 1024**3, 8)])
def test_duckdb_pinned_to_granted_slice(tmp_path, mem_bytes, threads):
    """Every session pins DuckDB memory_limit AND threads to the slice the
    admission manager granted -- never a static default, and never DuckDB's
    cgroup-blind default thread count."""
    result_path = tmp_path / "out.parquet"
    run_query_sync(
        "SELECT current_setting('memory_limit') AS m, current_setting('threads') AS t",
        result_path,
        memory_bytes=mem_bytes,
        threads=threads,
    )
    import duckdb

    row = duckdb.connect().execute(f"SELECT * FROM read_parquet('{result_path}')").fetchone()
    mem_setting, threads_setting = row
    assert int(threads_setting) == threads
    # DuckDB normalizes the unit in its display (GB -> GiB); round-trip the
    # expected bytes through DuckDB's own parser so the check is unit-agnostic.
    ref = duckdb.connect()
    ref.execute(f"SET memory_limit='{mem_bytes / 1024**3}GB'")
    expected_mem = ref.execute("SELECT current_setting('memory_limit')").fetchone()[0]
    assert mem_setting == expected_mem


async def test_timeout_interrupts_running_query(tmp_path):
    """A wall-clock timeout interrupts the in-flight DuckDB query (G-D2-a):
    the call raises TimeoutError far sooner than the query would complete on
    its own, proving the interrupt stopped real work rather than just the
    awaiting coroutine."""
    result_path = tmp_path / "out.parquet"
    # A 10^12-row cross join with a per-row computation: minutes of work if it
    # ran to completion, so finishing under the budget can only mean interrupt.
    sql = "SELECT sum(t1.range + t2.range) FROM range(1000000) t1, range(1000000) t2"

    start = time.monotonic()
    with pytest.raises(TimeoutError):
        await run_query(sql, result_path, timeout_s=0.5, memory_bytes=_MEM, threads=_THREADS)
    elapsed = time.monotonic() - start
    assert elapsed < 10, f"interrupt did not stop the query promptly ({elapsed:.1f}s)"


def test_invalid_sql_raises(tmp_path):
    result_path = tmp_path / "out.parquet"
    with pytest.raises(Exception):
        _run("THIS IS NOT VALID SQL !!!", result_path)


def test_empty_result_produces_zero_rows(tmp_path):
    result_path = tmp_path / "out.parquet"
    stats = _run("SELECT 1 WHERE 1=0", result_path)
    assert result_path.exists()
    assert stats["row_count"] == 0


def test_iceberg_metadata_parses_snapshot_and_deletes():
    """The probe maps iceberg_snapshots + iceberg_metadata rows to the wire
    shape, classifying data/delete files via the newer `manifest_content`."""
    from agent.executor.runner import _iceberg_metadata

    class FakeConn:
        # Newer iceberg extension: `manifest_content` carries DATA/DELETE.
        description = [("manifest_content",), ("count",)]

        def execute(self, sql):
            return self

        def fetchone(self):  # iceberg_snapshots row
            return (123456789, 1715780580000)

        def fetchall(self):  # iceberg_metadata grouped by manifest_content
            return [("DATA", 128), ("DELETE", 2)]

    meta = _iceberg_metadata(FakeConn(), "cat", "analytics", "events")
    assert meta["snapshot_id"] == 123456789
    assert meta["snapshot_at"].startswith("2024-")
    assert meta["data_file_count"] == 128
    assert meta["has_deletes"] is True


def test_iceberg_metadata_legacy_content_schema():
    """Older iceberg extensions without `manifest_content` classify via the
    `content` column (DATA/POSITION_DELETES/EQUALITY_DELETES)."""
    from agent.executor.runner import _iceberg_metadata

    class FakeConn:
        description = [("content",), ("count",)]

        def execute(self, sql):
            return self

        def fetchone(self):
            return (1, 1715780580000)

        def fetchall(self):
            return [("DATA", 5), ("POSITION_DELETES", 1)]

    meta = _iceberg_metadata(FakeConn(), "cat", "analytics", "events")
    assert meta["data_file_count"] == 5
    assert meta["has_deletes"] is True


def test_iceberg_metadata_best_effort_on_failure():
    """A probe failure (e.g. an older iceberg extension) degrades to all-null."""
    from agent.executor.runner import _iceberg_metadata

    class BoomConn:
        def execute(self, sql):
            raise RuntimeError("no such function: iceberg_snapshots")

    assert _iceberg_metadata(BoomConn(), "cat", "analytics", "events") == {
        "snapshot_id": None,
        "snapshot_at": None,
        "data_file_count": None,
        "has_deletes": None,
    }


def test_select_captures_normalized_profile(tmp_path):
    """A materialized SELECT returns a normalized profile: query summary with
    latency + peak memory, and an operator tree with actual cardinalities."""
    result_path = tmp_path / "out.parquet"
    stats = _run(
        "SELECT x % 10 g, count(*) c FROM range(50000) t(x) GROUP BY g ORDER BY c",
        result_path,
        enable_profiling=True,
    )
    profile = stats["profile"]
    assert profile is not None
    summary = profile["summary"]
    assert summary["latency_ms"] > 0
    assert summary["peak_memory_bytes"] >= 0
    assert set(summary) >= {"latency_ms", "peak_memory_bytes", "spill_bytes"}
    # rows_returned reflects the real result size (10 groups), not the COPY's
    # returned-row count of 1.
    assert summary["rows_returned"] == 10 == stats["row_count"]
    # The admission reservation the query ran under is recorded on the summary.
    assert summary["reserved_memory_bytes"] == _MEM
    assert summary["reserved_threads"] == _THREADS
    assert profile["tree"]["type"]  # operator tree present


def test_profiling_disabled_yields_null_profile(tmp_path):
    result_path = tmp_path / "out.parquet"
    stats = _run("SELECT 1 AS x", result_path, enable_profiling=False)
    assert stats["profile"] is None


def test_ddl_has_no_profile(tmp_path):
    result_path = tmp_path / "out.parquet"
    stats = _run("CREATE TABLE t (x INT)", result_path, enable_profiling=True)
    assert stats["profile"] is None


def test_profile_temp_file_cleaned_up(tmp_path):
    result_path = tmp_path / "out.parquet"
    _run("SELECT 1 AS x", result_path, enable_profiling=True)
    assert not (tmp_path / "out.profile.json").exists()


def test_forced_spill_reports_spill_bytes(tmp_path):
    """A large sort under a tiny memory_limit spills to a temp dir; the profile's
    query-level spill_bytes is positive (DuckDB reports spill at the root)."""
    import duckdb

    conn = duckdb.connect()
    conn.execute(f"SET temp_directory='{tmp_path / 'spill'}'")
    conn.execute("CREATE TABLE big AS SELECT i id, 'str' || i s FROM range(3000000) t(i)")
    result_path = tmp_path / "out.parquet"
    stats = run_query_sync(
        "SELECT s, count(*) c FROM big GROUP BY s ORDER BY c DESC, s",
        result_path,
        memory_bytes=200 * 1024**2,  # 200 MB forces spill
        threads=2,
        conn=conn,
        enable_profiling=True,
    )
    assert stats["profile"]["summary"]["spill_bytes"] > 0


def test_capture_profile_best_effort_on_missing_or_bad_file(tmp_path):
    """A missing or malformed profile file degrades to None, never raises."""
    import duckdb

    from agent.executor.runner import _capture_profile

    conn = duckdb.connect()
    assert _capture_profile(conn, tmp_path / "nope.json") is None
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert _capture_profile(conn, bad) is None


class _HealthConn:
    """Routes the health probe's queries by inspecting the SQL text.

    ``iceberg_metadata`` has both a ``LIMIT 0`` column-introspection call and an
    aggregate call; ``glob`` lists the data directory for orphan detection.
    """

    def __init__(
        self,
        *,
        files,
        listed=None,
        columns=("manifest_content", "file_path", "manifest_path", "file_size_in_bytes"),
        parquet_sizes=None,
    ):
        self.files = files  # list of (file_path, manifest_path, size) for DATA files
        self.listed = listed or []
        self._columns = columns
        self.parquet_sizes = parquet_sizes or []  # (file_name, size) from the footers
        self._last = ""

    def execute(self, sql, *args):
        self._last = sql
        if "LIMIT 0" in sql:
            self.description = [(c,) for c in self._columns]
        return self

    def fetchone(self):
        if "iceberg_snapshots" in self._last:
            return (7,)
        return None

    def fetchall(self):
        if "glob" in self._last:
            return [(f,) for f in self.listed]
        if "parquet_metadata" in self._last:
            return self.parquet_sizes
        if "iceberg_metadata" in self._last:
            return self.files
        return []


def test_collect_table_health_computes_distribution():
    from agent.executor.runner import collect_table_health

    files = [
        ("s3://b/t/data/a.parquet", "m1", 10),
        ("s3://b/t/data/b.parquet", "m1", 200 * 1024**2),
        ("s3://b/t/data/c.parquet", "m2", 5),
    ]
    health = collect_table_health(
        _HealthConn(files=files), "cat", "analytics", "events", target_file_bytes=128 * 1024**2
    )
    assert health["snapshot_count"] == 7
    assert health["data_file_count"] == 3
    assert health["manifest_count"] == 2
    assert health["total_data_bytes"] == 10 + 200 * 1024**2 + 5
    # two of three files are below the 128 MiB target.
    assert health["small_file_ratio"] == round(2 / 3, 4)
    # orphans not requested -> left null.
    assert health["orphan_file_count"] is None


def test_collect_table_health_size_fallback_via_parquet():
    from agent.executor.runner import collect_table_health

    # No size column in iceberg_metadata (the DuckDB <= 1.5.3 reality): on the deep
    # tier, sizes are derived from the Parquet footers instead.
    files = [
        ("s3://b/t/data/a.parquet", "m1", None),
        ("s3://b/t/data/b.parquet", "m1", None),
        ("s3://b/t/data/c.parquet", "m2", None),
    ]
    conn = _HealthConn(
        files=files,
        listed=[f[0] for f in files],  # no orphans
        columns=("manifest_content", "file_path", "manifest_path"),  # no size column
        parquet_sizes=[
            ("a.parquet", 10),
            ("b.parquet", 200 * 1024**2),
            ("c.parquet", 5),
        ],
    )
    health = collect_table_health(
        conn, "cat", "analytics", "events", target_file_bytes=128 * 1024**2, include_orphans=True
    )
    assert health["total_data_bytes"] == 10 + 200 * 1024**2 + 5
    assert health["avg_file_bytes"] == (10 + 200 * 1024**2 + 5) // 3
    # two of three files are below the 128 MiB target.
    assert health["small_file_ratio"] == round(2 / 3, 4)


def test_collect_table_health_size_fallback_is_deep_tier_only():
    from agent.executor.runner import collect_table_health

    # Without the deep tier, a missing size column leaves sizes unmeasured rather
    # than paying the per-file footer reads on every cheap cycle.
    files = [("s3://b/t/data/a.parquet", "m1", None)]
    conn = _HealthConn(
        files=files,
        columns=("manifest_content", "file_path", "manifest_path"),
        parquet_sizes=[("a.parquet", 10)],
    )
    health = collect_table_health(
        conn, "cat", "analytics", "events", target_file_bytes=128 * 1024**2
    )
    assert health["data_file_count"] == 1
    assert health["total_data_bytes"] is None
    assert health["small_file_ratio"] is None


def test_collect_table_health_orphan_estimate():
    from agent.executor.runner import collect_table_health

    files = [
        ("s3://b/t/data/a.parquet", "m1", 100),
        ("s3://b/t/data/b.parquet", "m1", 100),
    ]
    listed = [
        "s3://b/t/data/a.parquet",
        "s3://b/t/data/b.parquet",
        "s3://b/t/data/orphan-1.parquet",
        "s3://b/t/data/orphan-2.parquet",
    ]
    health = collect_table_health(
        _HealthConn(files=files, listed=listed),
        "cat",
        "analytics",
        "events",
        target_file_bytes=128 * 1024**2,
        include_orphans=True,
    )
    assert health["orphan_file_count"] == 2
    # estimated from the live average file size (100 bytes each).
    assert health["orphan_bytes"] == 200


def test_collect_table_health_best_effort_on_failure():
    from agent.executor.runner import collect_table_health

    class BoomConn:
        def execute(self, sql, *args):
            raise RuntimeError("no iceberg extension")

    health = collect_table_health(BoomConn(), "cat", "analytics", "events", target_file_bytes=1)
    assert health["schema"] == "analytics"
    assert health["data_file_count"] is None
    assert health["snapshot_count"] is None


def test_stats_for_reports_table_row_count(tmp_path):
    """When asked, the runner reports the true table row count (size stays null)."""
    result_path = tmp_path / "out.parquet"

    def seed(conn):
        conn.execute("CREATE TABLE main.events AS SELECT * FROM range(3) t(id)")

    stats = _run(
        "SELECT * FROM main.events",
        result_path,
        stats_for={"catalog": "memory", "schema": "main", "table": "events"},
        on_connect=seed,
    )
    assert stats["row_count"] == 3
    assert stats["table_row_count"] == 3
    assert stats["table_size_bytes"] is None
