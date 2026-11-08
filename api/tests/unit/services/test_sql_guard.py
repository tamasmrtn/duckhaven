"""Corpus tests for the SQL allowlist (G-D8-a)."""

from __future__ import annotations

import pytest

from api.services.sql_guard import SQLNotAllowed, assert_allowed, is_read_only, is_write

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
    # A table, schema or value named `secret` is not a secret statement.
    "CREATE TABLE secret (x INT)",
    "CREATE TABLE analytics.secret AS SELECT 'secret' AS s",
    "DROP TABLE secret",
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
    # Secrets: DuckDB types these as CREATE and DROP, the kinds allowed above, so
    # the guard has to look past the statement type to refuse them.
    ("CREATE SECRET s (TYPE s3, KEY_ID 'k', SECRET 'v')", "SECRET"),
    ("CREATE OR REPLACE SECRET s (TYPE s3)", "SECRET"),
    ("CREATE TEMPORARY SECRET s (TYPE s3)", "SECRET"),
    ("CREATE OR REPLACE PERSISTENT SECRET s (TYPE s3)", "SECRET"),
    ("CREATE SECRET IF NOT EXISTS s (TYPE s3)", "SECRET"),
    ("/* note */ -- more\ncreate secret s (type s3)", "SECRET"),
    ("DROP SECRET s", "SECRET"),
    ("DROP PERSISTENT SECRET IF EXISTS s", "SECRET"),
    ("SELECT 1; CREATE SECRET s (TYPE s3)", "SECRET"),
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


# `is_read_only` drives the migration freeze gate: SELECT-only bodies stay
# allowed while a catalog is read-only mid-migration; everything else is a write.
READ_ONLY = [
    "SELECT 1",
    "SELECT * FROM analytics.events",
    "WITH q AS (SELECT 1) SELECT * FROM q",
    "select 1; select 2",
    "DESCRIBE analytics.analytics.events",
    "SELECT * FROM analytics.events AT (VERSION => 1)",
]

NOT_READ_ONLY = [
    "INSERT INTO t VALUES (1)",
    "UPDATE t SET x=1",
    "DELETE FROM t",
    "CREATE TABLE t (x INT)",
    "ALTER TABLE t ADD COLUMN x INT",
    "DROP TABLE t",
    "MERGE INTO t USING u ON t.id=u.id WHEN MATCHED THEN DELETE",
    "SELECT 1; INSERT INTO t VALUES (1)",  # any write taints the batch
    "",
    "NOT EVEN SQL",  # unparseable -> not provably read-only
]


@pytest.mark.parametrize("sql", READ_ONLY)
def test_is_read_only_true(sql: str) -> None:
    assert is_read_only(sql) is True


@pytest.mark.parametrize("sql", NOT_READ_ONLY)
def test_is_read_only_false(sql: str) -> None:
    assert is_read_only(sql) is False


# `is_write` gates the MCP server's read-only mode. It is deliberately not the
# negation of `is_read_only`: a caller that treated "not a read" as "a write"
# would answer a typo by telling someone to ask for write access.
WRITES = [
    "INSERT INTO t VALUES (1)",
    "UPDATE t SET x=1",
    "DELETE FROM t",
    "CREATE TABLE t (x INT)",
    "ALTER TABLE t ADD COLUMN x INT",
    "DROP TABLE t",
    "MERGE INTO t USING u ON t.id=u.id WHEN MATCHED THEN DELETE",
    "SELECT 1; INSERT INTO t VALUES (1)",  # any write taints the batch
]

#: Neither a read nor a write. The first two are reads; the rest are refused by
#: `assert_allowed` for reasons unrelated to write permission, so the caller has
#: to forward them and report *its* message.
NOT_WRITES = [
    "SELECT 1",
    "select 1; select 2",
    "",
    "NOT EVEN SQL",
    "SELCT * FROM t",
    "EXPLAIN SELECT 1",
    "ATTACH 'other.db'",
    "PRAGMA database_list",
]


@pytest.mark.parametrize("sql", WRITES)
def test_is_write_true(sql: str) -> None:
    assert is_write(sql) is True


@pytest.mark.parametrize("sql", NOT_WRITES)
def test_is_write_false(sql: str) -> None:
    assert is_write(sql) is False


@pytest.mark.parametrize("sql", WRITES)
def test_every_write_is_also_not_read_only(sql: str) -> None:
    """The two agree wherever they overlap; they differ only outside it."""
    assert is_read_only(sql) is False


# ── File reads by path on the query path ──────────────────────────────────────
#
# Documented as limited to what the agent's network can reach, so only URL
# schemes pass; any filesystem path names the agent's own disk and is refused in
# every workspace, whatever the spelling.

QUERY_PATH_DENIED = [
    "SELECT content FROM read_text('/var/lib/data/notes.txt')",
    "SELECT * FROM read_parquet('/var/lib/data/9a111111-2222-4333-8444-555555555555.parquet')",
    "SELECT * FROM read_parquet('part-0.parquet')",
    "SELECT * FROM read_csv('./local.csv')",
    "SELECT * FROM read_csv('../local.csv')",
    "SELECT * FROM read_json('C:\\data\\x.json')",
    "SELECT content FROM read_text('.hidden-file')",
    "SELECT file FROM glob('*')",
    "SELECT * FROM read_parquet(['s3://bucket/a.parquet', '/var/lib/data/b.parquet'])",
    "SELECT * FROM read_parquet('file:///var/lib/data/x.parquet')",
    "SELECT * FROM '/var/lib/data/x.parquet'",
    "SELECT * FROM 'local.parquet'",
    'SELECT * FROM "local.parquet"',
    "SELECT * FROM local.parquet",
    "SUMMARIZE 'local.parquet'",
    "DESCRIBE 'local.parquet'",
    "SELECT 1; SELECT * FROM 'local.json'",
    # A path that cannot be read statically is refused, as in a session.
    "SELECT * FROM read_parquet(concat('s3://', 'bucket/x.parquet'))",
    # Only a write's target is a table; its source query is still a read.
    "INSERT INTO sales.analytics.events SELECT * FROM 'local.json'",
]

QUERY_PATH_ALLOWED = [
    "SELECT * FROM read_parquet('s3://bucket/data.parquet')",
    "SELECT * FROM read_csv('https://example.com/data.csv')",
    "SELECT * FROM read_parquet('abfss://container@account.dfs.core.windows.net/x.parquet')",
    "SELECT * FROM read_parquet('gs://bucket/x.parquet')",
    "SELECT * FROM 's3://bucket/data.parquet'",
    "SUMMARIZE 'https://example.com/x.parquet'",
    "SELECT * FROM glob('s3://bucket/*.parquet')",
    # Quoted identifiers and string data are not file reads.
    'SELECT * FROM "my table"',
    'SELECT * FROM "my cat"."my schema"."my table"',
    "SELECT 'local.parquet' AS name",
    "SELECT * FROM analytics.analytics.events",
    # A write target resolves as a table, never as a file.
    'CREATE TABLE "sales"."analytics"."events.json" (id INTEGER)',
    'DROP TABLE "sales"."analytics"."events.json"',
    'ALTER TABLE "sales"."analytics"."events.json" ADD COLUMN note VARCHAR',
    "INSERT INTO sales.analytics.json SELECT 1",
]


@pytest.mark.parametrize("sql", QUERY_PATH_DENIED)
def test_filesystem_path_reads_are_refused(sql: str) -> None:
    with pytest.raises(SQLNotAllowed) as info:
        assert_allowed(sql)
    assert "path" in str(info.value).lower()


@pytest.mark.parametrize("sql", QUERY_PATH_ALLOWED)
def test_url_reads_and_identifiers_still_pass(sql: str) -> None:
    assert_allowed(sql)


def test_unparseable_sql_with_a_file_reader_is_refused(monkeypatch) -> None:
    # DuckDB may accept SQL that sqlglot cannot model; the path rule must not
    # silently switch off for it.
    import api.services.sql_denylist as denylist

    def _fail(*_args, **_kwargs):
        raise ValueError("unparseable")

    monkeypatch.setattr(denylist.sqlglot, "parse", _fail)
    with pytest.raises(SQLNotAllowed):
        assert_allowed("SELECT * FROM read_parquet('s3://bucket/x.parquet')")
    with pytest.raises(SQLNotAllowed):
        assert_allowed("SELECT * FROM 'local.parquet'")
    assert_allowed("SELECT 1")


def test_internal_statement_templates_still_pass() -> None:
    # SQL the platform composes itself must keep clearing the gate.
    from api.services.sql_metadata import _FUNCTIONS_SQL, _KEYWORDS_SQL, _TYPES_SQL

    for sql in (
        'SELECT * FROM "ws"."analytics"."orders" LIMIT 20',
        "SELECT 1",
        'CREATE SCHEMA "ws"."analytics"',
        'CREATE TABLE "ws"."analytics"."orders" ("id" BIGINT NOT NULL)',
        'DROP TABLE "ws"."analytics"."orders"',
        'DROP SCHEMA "ws"."analytics" CASCADE',
        _FUNCTIONS_SQL,
        _KEYWORDS_SQL,
        _TYPES_SQL,
    ):
        assert_allowed(sql)
