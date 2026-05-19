import asyncio
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


async def test_timeout_raises(tmp_path, monkeypatch):
    import agent.executor.supervisor as sup_module

    def slow_runner(sql: str, result_path, memory_limit_gb: float) -> dict:
        time.sleep(0.5)
        return {"row_count": 0, "duration_ms": 500}

    monkeypatch.setattr(sup_module, "run_query_sync", slow_runner)

    result_path = tmp_path / "out.parquet"
    with pytest.raises(asyncio.TimeoutError):
        await run_query("SELECT 1", result_path, memory_limit_gb=1.0, timeout_s=0.05)


def test_invalid_sql_raises(tmp_path):
    result_path = tmp_path / "out.parquet"
    with pytest.raises(Exception):
        run_query_sync("THIS IS NOT VALID SQL !!!", result_path, memory_limit_gb=1.0)


def test_empty_result_produces_zero_rows(tmp_path):
    result_path = tmp_path / "out.parquet"
    stats = run_query_sync("SELECT 1 WHERE 1=0", result_path, memory_limit_gb=1.0)
    assert result_path.exists()
    assert stats["row_count"] == 0
