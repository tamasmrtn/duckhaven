"""Deriving lineage from executed SQL.

The behavioural contract this file pins down: which statements produce edges,
which deliberately produce none, and how names resolve. The "produces none" cases
matter as much as the positive ones — a fabricated edge is worse than a missing
one, so each omission here is a decision, not a gap.
"""

from __future__ import annotations

import uuid

import pytest

from api.services.lineage.extract import (
    LineageParseError,
    classify,
    edges_from_sql,
)

WAREHOUSE = uuid.UUID("11111111-1111-1111-1111-111111111111")
RAW = uuid.UUID("22222222-2222-2222-2222-222222222222")
CATALOGS = {"warehouse": WAREHOUSE, "raw": RAW}


def extract(sql: str, active_catalog: str | None = "warehouse"):
    return edges_from_sql(sql, active_catalog=active_catalog, catalog_ids=CATALOGS)


def pairs(sql: str, active_catalog: str | None = "warehouse"):
    """(source, target) as readable ``catalog.schema.table`` strings."""

    def name(ref):
        prefix = ref.system if ref.is_external else _slug(ref.catalog_id)
        return f"{prefix}.{ref.schema}.{ref.table}"

    return {(name(e.source), name(e.target)) for e in extract(sql, active_catalog)}


def _slug(catalog_id):
    return {WAREHOUSE: "warehouse", RAW: "raw"}[catalog_id]


# --- what produces lineage --------------------------------------------------


def test_create_table_as_select():
    assert pairs("CREATE TABLE warehouse.analytics.dim AS SELECT * FROM raw.analytics.src") == {
        ("raw.analytics.src", "warehouse.analytics.dim")
    }


def test_insert_select():
    assert pairs("INSERT INTO warehouse.analytics.dim SELECT * FROM raw.analytics.src") == {
        ("raw.analytics.src", "warehouse.analytics.dim")
    }


def test_create_view_keeps_the_view_as_the_target():
    # The parse-based approach records the view itself. A plan-based one could
    # not: DuckDB reports an entirely empty plan for CREATE VIEW.
    assert pairs("CREATE VIEW warehouse.analytics.v AS SELECT id FROM raw.analytics.src") == {
        ("raw.analytics.src", "warehouse.analytics.v")
    }


def test_create_or_replace_view():
    assert pairs(
        "CREATE OR REPLACE VIEW warehouse.analytics.v AS SELECT id FROM raw.analytics.src"
    ) == {("raw.analytics.src", "warehouse.analytics.v")}


def test_merge():
    assert pairs(
        "MERGE INTO warehouse.analytics.dim t USING raw.analytics.src s "
        "ON t.id = s.id WHEN MATCHED THEN UPDATE SET x = s.x"
    ) == {("raw.analytics.src", "warehouse.analytics.dim")}


def test_update_with_a_source_subquery():
    assert pairs(
        "UPDATE warehouse.analytics.dim SET x = (SELECT max(y) FROM raw.analytics.src)"
    ) == {("raw.analytics.src", "warehouse.analytics.dim")}


def test_delete_with_a_source_subquery():
    assert pairs(
        "DELETE FROM warehouse.analytics.dim WHERE id IN (SELECT id FROM raw.analytics.src)"
    ) == {("raw.analytics.src", "warehouse.analytics.dim")}


def test_join_yields_one_edge_per_source():
    assert pairs(
        "CREATE TABLE warehouse.analytics.dim AS "
        "SELECT a.id FROM raw.analytics.a a JOIN raw.analytics.b b USING (id)"
    ) == {
        ("raw.analytics.a", "warehouse.analytics.dim"),
        ("raw.analytics.b", "warehouse.analytics.dim"),
    }


def test_table_aliases_do_not_leak_into_names():
    assert pairs(
        "CREATE TABLE warehouse.analytics.dim AS SELECT x.id FROM raw.analytics.src AS x"
    ) == {("raw.analytics.src", "warehouse.analytics.dim")}


def test_nested_subquery_source_is_found():
    assert pairs(
        "CREATE TABLE warehouse.analytics.dim AS "
        "SELECT * FROM (SELECT id FROM (SELECT id FROM raw.analytics.src) inner_q) outer_q"
    ) == {("raw.analytics.src", "warehouse.analytics.dim")}


def test_multi_statement_script_pairs_each_target_with_its_own_sources():
    # The reason this module walks statement by statement instead of reusing
    # `extract_table_refs`, which flattens refs across the whole script.
    assert pairs(
        "CREATE TABLE warehouse.analytics.one AS SELECT * FROM raw.analytics.a;"
        "CREATE TABLE warehouse.analytics.two AS SELECT * FROM raw.analytics.b;"
    ) == {
        ("raw.analytics.a", "warehouse.analytics.one"),
        ("raw.analytics.b", "warehouse.analytics.two"),
    }


def test_repeated_source_yields_one_edge():
    assert pairs(
        "CREATE TABLE warehouse.analytics.dim AS "
        "SELECT a.id FROM raw.analytics.a a JOIN raw.analytics.a b USING (id)"
    ) == {("raw.analytics.a", "warehouse.analytics.dim")}


# --- CTEs -------------------------------------------------------------------


def test_cte_alias_is_not_a_source():
    assert pairs(
        "CREATE TABLE warehouse.analytics.dim AS "
        "WITH c AS (SELECT id FROM raw.analytics.src) SELECT * FROM c"
    ) == {("raw.analytics.src", "warehouse.analytics.dim")}


def test_chained_ctes_resolve_to_the_physical_source():
    assert pairs(
        "CREATE TABLE warehouse.analytics.dim AS "
        "WITH a AS (SELECT id FROM raw.analytics.src), b AS (SELECT id FROM a) "
        "SELECT * FROM b"
    ) == {("raw.analytics.src", "warehouse.analytics.dim")}


# --- name resolution --------------------------------------------------------


def test_unqualified_names_take_the_active_catalog_and_default_schema():
    assert pairs("CREATE TABLE dim AS SELECT * FROM src", active_catalog="raw") == {
        ("raw.analytics.src", "raw.analytics.dim")
    }


def test_schema_qualified_names_keep_their_schema():
    assert pairs("CREATE TABLE staging.dim AS SELECT * FROM curated.src") == {
        ("warehouse.curated.src", "warehouse.staging.dim")
    }


def test_unknown_catalog_slug_is_dropped_not_guessed():
    assert extract("CREATE TABLE nope.analytics.dim AS SELECT * FROM raw.analytics.src") == []


def test_no_active_catalog_and_no_qualification_yields_nothing():
    assert extract("CREATE TABLE dim AS SELECT * FROM src", active_catalog=None) == []


# --- what deliberately produces no lineage ----------------------------------


def test_select_is_not_lineage():
    assert extract("SELECT * FROM raw.analytics.src") == []


def test_insert_values_has_no_source_dataset():
    assert extract("INSERT INTO warehouse.analytics.dim VALUES (1, 2)") == []


def test_copy_from_a_file_has_no_source_dataset():
    assert extract("COPY warehouse.analytics.dim FROM 's3://bucket/f.parquet'") == []


def test_create_table_without_a_query_body_is_not_lineage():
    assert extract("CREATE TABLE warehouse.analytics.dim (id INT, x INT)") == []


def test_drop_and_alter_are_not_lineage():
    assert extract("DROP TABLE warehouse.analytics.dim") == []
    assert extract("ALTER TABLE warehouse.analytics.dim ADD COLUMN z INT") == []


def test_self_edge_is_skipped():
    assert extract("INSERT INTO raw.analytics.src SELECT * FROM raw.analytics.src") == []


def test_table_functions_are_not_catalog_objects():
    assert extract("CREATE TABLE warehouse.analytics.dim AS SELECT * FROM range(10)") == []


def test_system_catalog_and_metadata_schema_refs_are_excluded():
    assert (
        extract(
            "CREATE TABLE warehouse.analytics.dim AS SELECT * FROM duckhaven.info_schema.tables"
        )
        == []
    )
    assert (
        extract(
            "CREATE TABLE warehouse.analytics.dim AS "
            "SELECT * FROM warehouse.information_schema.tables"
        )
        == []
    )


# --- failure modes ----------------------------------------------------------


def test_unparseable_sql_raises_the_typed_error():
    # Fails open at the caller, which counts this and moves on — unlike the grant
    # check, which must fail closed on the same input.
    with pytest.raises(LineageParseError):
        extract("selct * from foo")


@pytest.mark.parametrize(
    "sql",
    [
        "CREATE TABLE warehouse.analytics.dim AS",  # truncated, but parses
        "VACUUM",  # falls back to a generic Command node
        "EXPLAIN SELECT 1",
        "CHECKPOINT",
    ],
)
def test_statements_that_parse_but_establish_nothing_yield_no_edges(sql):
    # sqlglot is lenient — plenty of input parses without classifying as a write.
    # None of it may invent an edge.
    assert extract(sql) == []


def test_empty_sql_produces_nothing():
    assert extract("") == []


# --- classification ---------------------------------------------------------


@pytest.mark.parametrize(
    ("sql", "expected"),
    [
        ("CREATE TABLE t AS SELECT 1", "create_table_as"),
        ("CREATE VIEW v AS SELECT 1", "create_view"),
        ("INSERT INTO t SELECT 1", "insert"),
        ("UPDATE t SET x = 1", "update"),
        ("MERGE INTO t USING s ON 1 = 1 WHEN MATCHED THEN UPDATE SET x = 1", "merge"),
        ("DELETE FROM t", "delete"),
        ("SELECT 1", None),
        ("CREATE SCHEMA s", None),
        ("CREATE TABLE t (id INT)", None),
    ],
)
def test_classify(sql, expected):
    import sqlglot

    assert classify(sqlglot.parse_one(sql, read="duckdb")) == expected


def test_operation_is_recorded_on_the_edge():
    edges = extract("CREATE VIEW warehouse.analytics.v AS SELECT id FROM raw.analytics.src")
    assert [e.operation for e in edges] == ["create_view"]
