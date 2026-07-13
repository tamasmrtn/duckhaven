"""run_statement_sync runs one statement on a held connection without closing it."""

import duckdb

from agent.executor.runner import run_statement_sync

_MEM = 1024**3
_THREADS = 2


def _run(conn, sql, result_path):
    return run_statement_sync(sql, result_path, conn=conn, memory_bytes=_MEM, threads=_THREADS)


def test_select_materializes_to_parquet_and_keeps_conn_open(tmp_path):
    conn = duckdb.connect()
    stats = _run(conn, "SELECT 42 AS answer", tmp_path / "s1.parquet")
    assert (tmp_path / "s1.parquet").exists()
    assert stats["row_count"] == 1
    assert stats["wrote_result"] is True
    # The connection is NOT closed — a further statement runs on the same session.
    assert conn.execute("SELECT 1").fetchone() == (1,)


def test_ddl_executes_with_no_result_file(tmp_path):
    conn = duckdb.connect()
    stats = _run(conn, "CREATE TABLE t (n INTEGER)", tmp_path / "s2.parquet")
    assert stats["wrote_result"] is False
    assert not (tmp_path / "s2.parquet").exists()
    # State persists on the held connection (temp-relation semantics).
    conn.execute("INSERT INTO t VALUES (1), (2)")
    assert conn.execute("SELECT count(*) FROM t").fetchone() == (2,)


def test_profile_records_session_reservation(tmp_path):
    conn = duckdb.connect()
    stats = _run(conn, "SELECT 1 AS n", tmp_path / "s3.parquet")
    assert stats["profile"] is not None
    assert stats["profile"]["summary"]["reserved_memory_bytes"] == _MEM
    assert stats["profile"]["summary"]["reserved_threads"] == _THREADS
