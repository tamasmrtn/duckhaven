"""Which queries the result cache may serve (`services/result_cache/eligibility.py`)."""

from __future__ import annotations

import pytest

from api.services.result_cache.eligibility import Ineligible, TableRef, analyze, resolve


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT a, count(*) FROM sales.orders WHERE d > DATE '2024-01-01' GROUP BY a",
        "SELECT * FROM t JOIN u USING (k) LEFT JOIN (SELECT * FROM v) s ON true",
        "SELECT 1 UNION ALL SELECT 2 ORDER BY 1 LIMIT 3",
        "SELECT * FROM (VALUES (1), (2)) v(x)",
        "SELECT * FROM range(3)",
        "SELECT * FROM t, unnest([1, 2]) u(x)",
        "WITH RECURSIVE r AS (SELECT 1 i UNION ALL SELECT i + 1 FROM r WHERE i < 3) "
        "SELECT * FROM r",
        # LIMIT without ORDER BY: any cached answer is still a correct answer.
        "SELECT * FROM t LIMIT 10",
        "SELECT a || b, a LIKE '%x', [1, 2], {'k': 1}, row_number() OVER (), CAST(b AS INT) FROM t",
        "SELECT date_trunc('day', ts), strftime(ts, '%Y'), approx_count_distinct(x) FROM t",
        "FROM t SELECT x WHERE x > (SELECT max(y) FROM u)",
    ],
)
def test_deterministic_reads_are_eligible(sql: str) -> None:
    analyze(sql)


@pytest.mark.parametrize(
    ("sql", "reason"),
    [
        ("INSERT INTO t VALUES (1)", "not_select"),
        ("CREATE TABLE t (a INT)", "not_select"),
        ("PRAGMA version", "not_select"),
        ("BEGIN", "not_select"),
        ("SELECT 1; SELECT 2", "multiple_statements"),
        ("DESCRIBE t", "unsupported_relation"),
        ("SUMMARIZE t", "unsupported_relation"),
        ("SELECT random()", "volatile_function"),
        ("SELECT uuid()", "volatile_function"),
        ("SELECT now()", "volatile_function"),
        ("SELECT today()", "volatile_function"),
        ("SELECT nextval('s')", "volatile_function"),
        # Parsed as column references; the binder makes them functions.
        ("SELECT current_date", "volatile_function"),
        ("SELECT current_timestamp", "volatile_function"),
        ("SELECT localtimestamp", "volatile_function"),
        ("SELECT current_user", "volatile_function"),
        # Labelled CONSISTENT by duckdb_functions() although they are not.
        ("SELECT current_localtimestamp()", "volatile_function"),
        ("SELECT getvariable('x')", "volatile_function"),
        ("SELECT current_setting('TimeZone')", "volatile_function"),
        # A macro over a denied function.
        ("SELECT ago(INTERVAL 1 DAY)", "volatile_function"),
        ("SELECT my_udf(1)", "unknown_function"),
        ("SELECT lk.main.f(1)", "unknown_function"),
        ("SELECT * FROM read_parquet('x.parquet')", "table_function"),
        ("SELECT * FROM duckdb_tables()", "table_function"),
        ("SELECT * FROM iceberg_scan('s3://b/t')", "table_function"),
        ("SELECT * FROM t USING SAMPLE 10", "sample"),
        ("SELECT * FROM t TABLESAMPLE 10%", "sample"),
        ("SELECT * FROM t AT (VERSION => 1)", "time_travel"),
        ("SELECT $1", "parameter"),
        ("SELECT ?", "parameter"),
        ("not sql at all", "not_select"),
    ],
)
def test_ineligible_queries_say_why(sql: str, reason: str) -> None:
    with pytest.raises(Ineligible) as exc:
        analyze(sql)
    assert exc.value.reason == reason


def test_the_identity_ignores_layout_comments_and_keyword_case() -> None:
    a = analyze("select   UPPER(name) -- note\n from t  where x = 'A b'")
    b = analyze("SELECT upper(name) FROM t WHERE x = 'A b'")
    assert a.canonical == b.canonical


@pytest.mark.parametrize(
    ("left", "right"),
    [
        ("SELECT x FROM t WHERE x = 'a'", "SELECT x FROM t WHERE x = 'A'"),
        ("SELECT x AS a FROM t", "SELECT x AS A FROM t"),
        ("SELECT x FROM t LIMIT 10", "SELECT x FROM t LIMIT 11"),
        ("SELECT x FROM t ORDER BY x", "SELECT x FROM t ORDER BY x DESC"),
    ],
)
def test_the_identity_keeps_literals_aliases_and_semantics(left: str, right: str) -> None:
    assert analyze(left).canonical != analyze(right).canonical


def _resolve(sql: str, **kw) -> list[TableRef]:
    return resolve(
        analyze(sql),
        attached=kw.get("attached", {"c1", "c2"}),
        current_catalog=kw.get("current_catalog", "c1"),
        current_schema=kw.get("current_schema", "main"),
    )


def test_names_resolve_like_the_agents_use_context() -> None:
    refs = _resolve("SELECT * FROM t JOIN s.u USING (k) JOIN c2.x.y USING (k) JOIN t t2 USING (k)")
    assert refs == [
        TableRef("c1", "main", "t"),
        TableRef("c1", "s", "u"),
        TableRef("c2", "x", "y"),
    ]


def test_a_cte_shadowed_name_is_an_optional_dependency() -> None:
    """`WITH t AS (SELECT * FROM t)` reads the real `t` inside the CTE body, so a
    shadowed name is still tracked as a real table when one exists."""
    refs = _resolve("WITH t AS (SELECT v FROM t) SELECT * FROM t")
    assert refs == [TableRef("c1", "main", "t", optional=True)]


def test_a_name_used_both_ways_is_required() -> None:
    refs = _resolve("WITH t AS (SELECT 1) SELECT * FROM t JOIN c1.main.t USING (k)")
    assert refs == [TableRef("c1", "main", "t")]


@pytest.mark.parametrize(
    ("sql", "kw", "reason"),
    [
        ("SELECT * FROM duckhaven.info_schema.tables", {}, "system_catalog"),
        ("SELECT * FROM information_schema.tables", {}, "system_catalog"),
        ("SELECT * FROM c1.info_schema.tables", {}, "system_catalog"),
        ("SELECT * FROM t", {"current_catalog": "duckhaven"}, "system_catalog"),
        ("SELECT * FROM other.s.t", {}, "unknown_catalog"),
        ("SELECT * FROM t", {"current_catalog": "gone"}, "unknown_catalog"),
        # `c2.t` could be schema c2 in c1, or catalog c2: refuse rather than guess.
        ("SELECT * FROM c2.t", {}, "ambiguous_name"),
        ("SELECT * FROM temp.t", {}, "ambiguous_name"),
    ],
)
def test_unresolvable_names_are_ineligible(sql: str, kw: dict, reason: str) -> None:
    with pytest.raises(Ineligible) as exc:
        _resolve(sql, **kw)
    assert exc.value.reason == reason
