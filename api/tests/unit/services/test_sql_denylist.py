"""Foreign-database and DuckLake-metadata denials, shared by both statement gates.

These close two holes that open the moment agents load the `postgres` extension
for DuckLake, and that statement-type checks alone cannot see:
`postgres_query(...)` is a SELECT, and an UPDATE against `__ducklake_metadata_*`
is an UPDATE. Both forms were verified reachable on DuckDB 1.5.5.
"""

from __future__ import annotations

import pytest

from api.services.sql_denylist import ForeignAccessDenied, check_sql
from api.services.sql_guard import SQLNotAllowed, assert_allowed
from api.services.statement_policy import StatementNotAllowed, assert_statement_allowed

_STAGING = ["s3://warehouse/analytics/_staging/sess-1/"]
_MANAGED = {"sales", "analytics", "raw"}

# Every one of these is an otherwise-allowed statement type.
DENIED = [
    # Foreign-database functions: arbitrary SQL as the agent's Postgres role.
    "SELECT * FROM postgres_query('__ducklake_metadata_raw', 'SELECT 1')",
    "SELECT * FROM postgres_scan('dbname=x', 'public', 't')",
    "SELECT * FROM postgres_scan_pushdown('dbname=x', 'public', 't')",
    "SELECT * FROM mysql_query('m', 'SELECT 1')",
    "SELECT * FROM sqlite_scan('f.db', 't')",
    # Nested rather than top-level.
    "SELECT * FROM (SELECT * FROM postgres_query('m', 'SELECT 1')) x",
    "WITH q AS (SELECT * FROM postgres_query('m', 'SELECT 1')) SELECT * FROM q",
    "INSERT INTO t SELECT * FROM postgres_query('m', 'SELECT 1')",
    # DuckLake's internal catalog metadata, read and write.
    "SELECT count(*) FROM __ducklake_metadata_raw.cat_raw.ducklake_data_file",
    "UPDATE __ducklake_metadata_raw.cat_raw.ducklake_data_file SET record_count = 0",
    "DELETE FROM __ducklake_metadata_raw.cat_raw.ducklake_snapshot",
    "DROP TABLE __ducklake_metadata_raw.cat_raw.ducklake_table",
    # Two-part (metadata catalog in the `db` slot) and bare (the `name` slot).
    "SELECT * FROM __ducklake_metadata_raw.ducklake_table",
    "SELECT * FROM __ducklake_metadata_raw",
    # Case must not be an escape.
    "SELECT * FROM POSTGRES_QUERY('m', 'SELECT 1')",
    "SELECT * FROM __DUCKLAKE_METADATA_RAW.cat_raw.ducklake_table",
]

# Denial must not over-match: none of these reaches a foreign database.
ALLOWED = [
    "SELECT 'postgres_query' AS harmless",
    "SELECT * FROM analytics.metadata",
    "SELECT * FROM raw.info_schema.tables",
    "SELECT postgres_query_count FROM t",
    "CREATE TABLE ducklake_table (x INT)",
    "SELECT * FROM raw.analytics.ducklake_data_file",
    "SELECT * FROM t AT (VERSION => 3)",
]


@pytest.mark.parametrize("sql", DENIED)
def test_check_sql_denies(sql: str) -> None:
    with pytest.raises(ForeignAccessDenied):
        check_sql(sql)


@pytest.mark.parametrize("sql", ALLOWED)
def test_check_sql_allows(sql: str) -> None:
    check_sql(sql)


@pytest.mark.parametrize("sql", DENIED)
def test_sql_guard_rejects(sql: str) -> None:
    """The one-shot /queries path re-raises as its own 422-shaped error."""
    with pytest.raises(SQLNotAllowed):
        assert_allowed(sql)


@pytest.mark.parametrize("sql", DENIED)
def test_statement_policy_rejects(sql: str) -> None:
    """The SQL-session path rejects the same corpus, with a rule slug."""
    with pytest.raises(StatementNotAllowed) as excinfo:
        assert_statement_allowed(sql, staging_prefixes=_STAGING, managed_catalogs=_MANAGED)
    assert excinfo.value.rule in {"foreign_database_function", "ducklake_metadata_access"}


def test_rules_are_distinct() -> None:
    with pytest.raises(ForeignAccessDenied) as fn:
        check_sql("SELECT * FROM postgres_query('m', 'SELECT 1')")
    assert fn.value.rule == "foreign_database_function"
    with pytest.raises(ForeignAccessDenied) as md:
        check_sql("SELECT * FROM __ducklake_metadata_raw.cat_raw.ducklake_table")
    assert md.value.rule == "ducklake_metadata_access"


def test_unparseable_sql_falls_back_to_a_lexical_check() -> None:
    """sqlglot cannot parse everything DuckDB can. When it fails, text that
    merely mentions a denied name is refused rather than waved through."""
    sql = "SELECT * FROM postgres_query('m', 'SELECT 1') ((( unbalanced"
    with pytest.raises(ForeignAccessDenied):
        check_sql(sql)


def test_existing_allowlist_behaviour_is_unchanged() -> None:
    """A spot-check that the new pass did not narrow the ordinary corpus."""
    for sql in ("SELECT 1", "INSERT INTO t VALUES (1)", "CREATE TABLE t (x INT)"):
        assert_allowed(sql)
