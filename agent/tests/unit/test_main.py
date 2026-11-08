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


# ── session token location ───────────────────────────────────────────────────


def test_session_token_defaults_to_the_results_dir(tmp_path: Path) -> None:
    from agent.main import _session_token_path

    assert _session_token_path("", tmp_path) == tmp_path / ".session-token"


def test_a_token_at_the_default_location_moves_to_the_configured_path(tmp_path: Path) -> None:
    """An upgraded agent keeps its identity, and no copy stays beside the results."""
    from agent.main import _session_token_path

    results = tmp_path / "results"
    results.mkdir()
    (results / ".session-token").write_text("dh_sess_abc\n")
    configured = tmp_path / "state" / "session-token"

    assert _session_token_path(str(configured), results) == configured
    assert configured.read_text() == "dh_sess_abc"
    assert configured.stat().st_mode & 0o777 == 0o600
    assert not (results / ".session-token").exists()

    # Once moved, a restart leaves it where it is.
    assert _session_token_path(str(configured), results) == configured
    assert configured.read_text() == "dh_sess_abc"


def test_a_token_already_at_the_configured_path_wins(tmp_path: Path) -> None:
    from agent.main import _session_token_path

    results = tmp_path / "results"
    results.mkdir()
    (results / ".session-token").write_text("dh_sess_old")
    configured = tmp_path / "session-token"
    configured.write_text("dh_sess_new")

    assert _session_token_path(str(configured), results) == configured
    assert configured.read_text() == "dh_sess_new"
    assert not (results / ".session-token").exists()


def test_an_unwritable_configured_path_keeps_the_token_where_it_is(tmp_path: Path) -> None:
    """An older image without the token directory must not lose its identity."""
    from agent.main import _session_token_path

    if os.geteuid() == 0:
        pytest.skip("root ignores directory permissions")
    results = tmp_path / "results"
    results.mkdir()
    legacy = results / ".session-token"
    legacy.write_text("dh_sess_abc")
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o555)
    try:
        assert _session_token_path(str(locked / "session-token"), results) == legacy
        assert legacy.read_text() == "dh_sess_abc"
    finally:
        locked.chmod(0o755)
