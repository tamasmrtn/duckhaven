"""Corpus tests for the SQL allowlist (G-D8-a)."""

from __future__ import annotations

import pytest

from api.services.sql_guard import SQLNotAllowed, assert_allowed

ALLOWED = [
    "SELECT 1",
    "SELECT * FROM analytics.events",
    "SELECT id FROM t WHERE id IN (SELECT id FROM other)",
    "WITH q AS (SELECT 1) SELECT * FROM q",
    "INSERT INTO t VALUES (1, 'a')",
    "INSERT INTO t SELECT * FROM u",
    "WITH src AS (SELECT 1 AS id) INSERT INTO t SELECT * FROM src",
    "SELECT 1 /* /* SET memory_limit='1GB' */ */",  # nested-comment harmless
    "SELECT 'DROP TABLE x' AS s",  # literal containing DROP is harmless
    "-- comment\nSELECT 1",
    # DDL — catalog object management.
    "CREATE TABLE t (x INT)",
    "CREATE SCHEMA analytics",
    "CREATE OR REPLACE VIEW v AS SELECT 1",
    "ALTER TABLE t ADD COLUMN x INT",
    "DROP TABLE t",
    "DROP TABLE IF EXISTS t",
    # Destructive DML.
    "UPDATE t SET x=1",
    "DELETE FROM t",
    "TRUNCATE t",  # DuckDB classifies TRUNCATE as a DELETE statement
    "MERGE INTO t USING u ON t.id = u.id WHEN MATCHED THEN DELETE",
    # multi-statement, all allowed (DDL + DML):
    "SELECT 1; INSERT INTO t VALUES (1)",
    "CREATE TABLE t (x INT); INSERT INTO t VALUES (1)",
    # Iceberg time-travel: DuckDB's AT (...) clause is a SELECT (snapshot-history
    # "query at this snapshot"). Locks I8 — these must not classify as an escape.
    "SELECT * FROM analytics.events AT (VERSION => 1234567890)",
    "SELECT * FROM analytics.events AT (TIMESTAMP => '2026-05-15T14:03:00Z') LIMIT 100",
    # Metadata browsing: information_schema is DuckDB's native, read-only SELECT
    # surface (global views spanning attached catalogs, scoped per catalog with
    # table_catalog). It must pass the guard like any other SELECT.
    "SELECT table_schema, table_name FROM information_schema.tables",
    "SELECT column_name FROM information_schema.columns WHERE table_catalog = 'analytics'",
    "SELECT schema_name FROM information_schema.schemata",
    "SELECT * FROM iceberg_snapshots('analytics.analytics.events')",
    # Column introspection: DuckDB classifies DESCRIBE/DESC/SHOW/SUMMARIZE as
    # SELECT, so they already pass. This is the documented column-detail path for
    # Iceberg tables (information_schema.columns can't introspect them yet).
    "DESCRIBE analytics.analytics.events",
    "DESC analytics.analytics.events",
    "SELECT * FROM (DESCRIBE analytics.analytics.events)",
    "SHOW ALL TABLES",
]


DISALLOWED = [
    # DuckDB rewrites PRAGMA into SET internally; still rejected.
    ("PRAGMA memory_limit='1GB'", "SET"),
    ("SET memory_limit='1GB'", None),
    ("COPY t TO 'f.parquet'", "COPY"),
    ("ATTACH 'x' AS y", "ATTACH"),
    ("DETACH y", None),
    ("LOAD httpfs", "LOAD"),
    ("INSTALL httpfs", None),
    ("CALL pragma_version()", None),
    ("VACUUM", None),
    # multi-statement mixed: an allowed SELECT alongside a blocked COPY rejects.
    ("SELECT 1; COPY t TO 'f.parquet'", "COPY"),
]


@pytest.mark.parametrize("sql", ALLOWED)
def test_allowed_sql_passes(sql: str) -> None:
    assert_allowed(sql)


@pytest.mark.parametrize("sql,expect_in_detail", DISALLOWED)
def test_disallowed_sql_rejected(sql: str, expect_in_detail: str | None) -> None:
    with pytest.raises(SQLNotAllowed) as info:
        assert_allowed(sql)
    if expect_in_detail:
        assert expect_in_detail in str(info.value)


def test_empty_sql_rejected() -> None:
    with pytest.raises(SQLNotAllowed):
        assert_allowed("")
    with pytest.raises(SQLNotAllowed):
        assert_allowed("   \n\t  ")


def test_parse_error_surfaces_as_not_allowed() -> None:
    with pytest.raises(SQLNotAllowed) as info:
        assert_allowed("NOT EVEN SQL")
    assert "parse error" in str(info.value).lower()
