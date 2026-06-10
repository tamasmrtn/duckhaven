"""Startup must run the agent from a writable working directory.

Regression for the e2e `aggregates over a large dataset` failure: DuckDB creates
some files relative to the process working directory (e.g. the transient `data`
staging directory an Iceberg `CREATE TABLE … AS SELECT` uses). The container's
default cwd is root-owned while the agent runs as a non-root user, so those
writes failed with `IO Error: Failed to create directory "data": Permission
denied`. A partitioned COPY reproduces the same relative-directory write here
without needing Polaris.
"""

from __future__ import annotations

import os
from pathlib import Path

import duckdb
import pytest

from agent.main import _use_writable_workdir


def _relative_dir_write() -> None:
    """A DuckDB write that creates a directory relative to the cwd."""
    duckdb.connect().execute(
        "COPY (SELECT 1 AS a, 2 AS b) TO 'data' (FORMAT PARQUET, PARTITION_BY (a))"
    )


def test_use_writable_workdir_enables_relative_dir_writes(tmp_path: Path) -> None:
    original_cwd = os.getcwd()
    read_only = tmp_path / "ro"
    read_only.mkdir()
    read_only.chmod(0o555)
    results_dir = tmp_path / "results"
    results_dir.mkdir()
    try:
        # From a read-only cwd, the relative-directory write fails exactly as it
        # did in the container.
        os.chdir(read_only)
        with pytest.raises(duckdb.IOException, match='Failed to create directory "data"'):
            _relative_dir_write()

        # After pointing the process at the writable results dir, it succeeds.
        _use_writable_workdir(results_dir)
        assert Path(os.getcwd()) == results_dir.resolve()
        _relative_dir_write()
        assert (results_dir / "data").is_dir()
    finally:
        os.chdir(original_cwd)
        read_only.chmod(0o755)
