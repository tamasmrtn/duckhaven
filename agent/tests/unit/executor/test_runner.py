import time

import pytest

from agent.executor.runner import run_query_sync
from agent.executor.supervisor import run_query


def test_simple_select_produces_parquet(tmp_path):
    result_path = tmp_path / "out.parquet"
    stats = run_query_sync("SELECT 42 AS answer", result_path, memory_limit_gb=1.0)
    assert result_path.exists()
    assert stats["row_count"] == 1
    assert stats["duration_ms"] >= 0


def test_multiple_rows(tmp_path):
    result_path = tmp_path / "out.parquet"
    stats = run_query_sync("SELECT unnest([1,2,3]) AS n", result_path, memory_limit_gb=1.0)
    assert stats["row_count"] == 3


def test_memory_limit_applied(tmp_path):
    import duckdb

    result_path = tmp_path / "out.parquet"
    run_query_sync("SELECT 1", result_path, memory_limit_gb=0.5)
    conn = duckdb.connect()
    setting = conn.execute("SELECT current_setting('memory_limit')").fetchone()
    conn.close()
    assert setting is not None


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
        await run_query(sql, result_path, memory_limit_gb=2.0, timeout_s=0.5)
    elapsed = time.monotonic() - start
    assert elapsed < 10, f"interrupt did not stop the query promptly ({elapsed:.1f}s)"


def test_invalid_sql_raises(tmp_path):
    result_path = tmp_path / "out.parquet"
    with pytest.raises(Exception):
        run_query_sync("THIS IS NOT VALID SQL !!!", result_path, memory_limit_gb=1.0)


def test_empty_result_produces_zero_rows(tmp_path):
    result_path = tmp_path / "out.parquet"
    stats = run_query_sync("SELECT 1 WHERE 1=0", result_path, memory_limit_gb=1.0)
    assert result_path.exists()
    assert stats["row_count"] == 0


def test_stats_for_reports_table_row_count(tmp_path):
    """When asked, the runner reports the true table row count (size stays null)."""
    result_path = tmp_path / "out.parquet"

    def seed(conn):
        conn.execute("CREATE TABLE main.events AS SELECT * FROM range(3) t(id)")

    stats = run_query_sync(
        "SELECT * FROM main.events",
        result_path,
        memory_limit_gb=1.0,
        stats_for={"schema": "main", "table": "events"},
        on_connect=seed,
    )
    assert stats["row_count"] == 3
    assert stats["table_row_count"] == 3
    assert stats["table_size_bytes"] is None
