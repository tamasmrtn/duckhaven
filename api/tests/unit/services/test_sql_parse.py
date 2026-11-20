"""The shared sqlglot parse, and the rule that every caller only reads it."""

from __future__ import annotations

import copy

import pytest

from api.services import grants, sql_classify, sql_denylist
from api.services.sql_parse import parse_duckdb

STATEMENTS = [
    "SELECT * FROM lake.s.events WHERE id = 42",
    "WITH a AS (SELECT user_id, sum(amount) AS s FROM lake.s.events GROUP BY 1) SELECT * FROM a",
    "INSERT INTO lake.s.t SELECT * FROM read_parquet('s3://bucket/x.parquet')",
    "CREATE TABLE lake.s.t2 AS SELECT 1 AS id",
    "DESCRIBE lake.s.events",
    "SELECT * FROM information_schema.tables",
    "USE lake.s; SELECT 1",
]


def test_one_parse_serves_every_caller():
    sql = "SELECT 1 AS one_parse_serves_every_caller"
    assert parse_duckdb(sql) is parse_duckdb(sql)


def test_a_failed_parse_is_not_cached(monkeypatch):
    import api.services.sql_parse as module

    calls = []

    def flaky(sql, read):
        calls.append(sql)
        raise ValueError("unparseable")

    monkeypatch.setattr(module.sqlglot, "parse", flaky)
    for _ in range(2):
        with pytest.raises(ValueError):
            parse_duckdb("SELECT 'a failed parse is not cached'")
    assert len(calls) == 2


@pytest.mark.parametrize("sql", STATEMENTS)
def test_no_caller_modifies_the_shared_tree(sql):
    """A caller that changed a tree would change it for every later caller too."""
    before = copy.deepcopy(parse_duckdb(sql))
    for check in (
        lambda: sql_denylist.check_sql(sql),
        lambda: sql_classify.classify_statement(sql),
        lambda: grants.extract_table_refs(sql),
        lambda: grants.metadata_enumeration_reason(sql),
        lambda: grants.file_read_reason(sql, []),
    ):
        try:
            check()
        except Exception:  # noqa: BLE001 - a refusal is fine; only mutation matters
            pass
    assert parse_duckdb(sql) == before
