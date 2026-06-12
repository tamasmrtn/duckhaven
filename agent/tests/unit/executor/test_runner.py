import time

import pytest

from agent.executor.runner import run_query_sync
from agent.executor.supervisor import run_query


def test_simple_select_produces_parquet(tmp_path):
    result_path = tmp_path / "out.parquet"
    stats = run_query_sync("SELECT 42 AS answer", result_path)
    assert result_path.exists()
    assert stats["row_count"] == 1
    assert stats["wrote_result"] is True
    assert stats["duration_ms"] >= 0


def test_select_reports_result_bytes(tmp_path):
    """A materialized SELECT reports the Parquet result file's size."""
    result_path = tmp_path / "out.parquet"
    stats = run_query_sync("SELECT * FROM range(100) t(x)", result_path)
    assert stats["result_bytes"] == result_path.stat().st_size
    assert stats["result_bytes"] > 0


def test_ddl_runs_without_result_file(tmp_path):
    """Pure DDL executes but writes no Parquet and reports zero rows."""
    result_path = tmp_path / "out.parquet"
    stats = run_query_sync("CREATE TABLE t (x INT)", result_path)
    assert not result_path.exists()
    assert stats["wrote_result"] is False
    assert stats["row_count"] == 0
    assert stats["result_bytes"] is None


def test_dml_reports_affected_count(tmp_path):
    """A multi-statement DDL+DML script runs directly and reports the affected
    row count from the final statement (no result file)."""
    result_path = tmp_path / "out.parquet"
    stats = run_query_sync(
        "CREATE TABLE t (x INT); INSERT INTO t VALUES (1), (2), (3)",
        result_path,
    )
    assert not result_path.exists()
    assert stats["wrote_result"] is False
    assert stats["row_count"] == 3


def test_multiple_rows(tmp_path):
    result_path = tmp_path / "out.parquet"
    stats = run_query_sync("SELECT unnest([1,2,3]) AS n", result_path)
    assert stats["row_count"] == 3


@pytest.mark.parametrize(("mem_bytes", "cores"), [(4 * 1024**3, 3), (16 * 1024**3, 8)])
def test_duckdb_pinned_to_detected_allocation(tmp_path, monkeypatch, mem_bytes, cores):
    """Every session pins DuckDB memory_limit AND threads to the detected
    effective allocation (cgroup-limited or full host) -- never the old static
    6 GB and never DuckDB's cgroup-blind default thread count."""
    import agent.executor.runner as runner

    monkeypatch.setattr(runner, "effective_memory_bytes", lambda: mem_bytes)
    monkeypatch.setattr(runner, "effective_cores", lambda: cores)

    result_path = tmp_path / "out.parquet"
    run_query_sync(
        "SELECT current_setting('memory_limit') AS m, current_setting('threads') AS t",
        result_path,
    )
    import duckdb

    row = duckdb.connect().execute(f"SELECT * FROM read_parquet('{result_path}')").fetchone()
    mem_setting, threads_setting = row
    assert int(threads_setting) == cores
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
        await run_query(sql, result_path, timeout_s=0.5)
    elapsed = time.monotonic() - start
    assert elapsed < 10, f"interrupt did not stop the query promptly ({elapsed:.1f}s)"


def test_invalid_sql_raises(tmp_path):
    result_path = tmp_path / "out.parquet"
    with pytest.raises(Exception):
        run_query_sync("THIS IS NOT VALID SQL !!!", result_path)


def test_empty_result_produces_zero_rows(tmp_path):
    result_path = tmp_path / "out.parquet"
    stats = run_query_sync("SELECT 1 WHERE 1=0", result_path)
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

    meta = _iceberg_metadata(FakeConn(), "analytics", "events")
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

    meta = _iceberg_metadata(FakeConn(), "analytics", "events")
    assert meta["data_file_count"] == 5
    assert meta["has_deletes"] is True


def test_iceberg_metadata_best_effort_on_failure():
    """A probe failure (e.g. an older iceberg extension) degrades to all-null."""
    from agent.executor.runner import _iceberg_metadata

    class BoomConn:
        def execute(self, sql):
            raise RuntimeError("no such function: iceberg_snapshots")

    assert _iceberg_metadata(BoomConn(), "analytics", "events") == {
        "snapshot_id": None,
        "snapshot_at": None,
        "data_file_count": None,
        "has_deletes": None,
    }


def test_stats_for_reports_table_row_count(tmp_path):
    """When asked, the runner reports the true table row count (size stays null)."""
    result_path = tmp_path / "out.parquet"

    def seed(conn):
        conn.execute("CREATE TABLE main.events AS SELECT * FROM range(3) t(id)")

    stats = run_query_sync(
        "SELECT * FROM main.events",
        result_path,
        stats_for={"schema": "main", "table": "events"},
        on_connect=seed,
    )
    assert stats["row_count"] == 3
    assert stats["table_row_count"] == 3
    assert stats["table_size_bytes"] is None
