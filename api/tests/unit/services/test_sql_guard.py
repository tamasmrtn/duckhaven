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
    # multi-statement, all allowed:
    "SELECT 1; INSERT INTO t VALUES (1)",
]


DISALLOWED = [
    ("UPDATE t SET x=1", "UPDATE"),
    ("DELETE FROM t", "DELETE"),
    ("DROP TABLE t", "DROP"),
    ("CREATE TABLE t (x INT)", "CREATE"),
    ("ALTER TABLE t ADD COLUMN x INT", "ALTER"),
    ("TRUNCATE t", None),  # any non-allowed type triggers rejection
    # DuckDB rewrites PRAGMA into SET internally; still rejected.
    ("PRAGMA memory_limit='1GB'", "SET"),
    ("SET memory_limit='1GB'", None),
    ("COPY t TO 'f.parquet'", "COPY"),
    ("ATTACH 'x' AS y", "ATTACH"),
    ("DETACH y", None),
    ("LOAD httpfs", "LOAD"),
    ("INSTALL httpfs", None),
    # multi-statement mixed: SELECT then DROP must reject
    ("SELECT 1; DROP TABLE x", "DROP"),
    # MERGE — even if DuckDB grows MERGE support later, it stays
    # out of the allowlist until we choose to widen it.
    ("MERGE INTO t USING u ON t.id = u.id WHEN MATCHED THEN DELETE", None),
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
