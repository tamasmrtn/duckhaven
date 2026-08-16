"""What a column relationship means, stated one SQL construct at a time.

These tests are the specification. The rules they pin down come from sqlglot's
lineage walker rather than from code in this repository, which is exactly why
they are worth writing out: an upgrade that moves the line between "this column's
values reached the output" and "this column decided which rows did" has to fail
here, loudly, instead of quietly changing what the lineage graph claims about
somebody's warehouse.

The distinction under test throughout: a column contributes when it can change
the *value* of an output column, and does not when it only changes *which rows*
come out. That is the whole difference between column lineage and the table graph
it refines.

No database. Extraction resolves names against a dict of catalog ids and reads
schemas through :class:`SchemaLookup`, so both are supplied directly.
"""

from __future__ import annotations

import uuid

import pytest

from api.services.lineage.columns import (
    DERIVED,
    UNSUPPORTED,
    MappingSchemaLookup,
    columns_for_sql,
)
from api.services.lineage.keys import internal_ref
from api.services.workspace import DEFAULT_SCHEMA

WAREHOUSE = uuid.uuid4()
RAW = uuid.uuid4()
CATALOG_IDS = {"warehouse": WAREHOUSE, "raw": RAW}


def ref(table: str, *, catalog: uuid.UUID = WAREHOUSE, schema: str = DEFAULT_SCHEMA):
    return internal_ref(catalog, schema, table)


class CountingLookup:
    """A schema source that records how often it was asked.

    The count is load-bearing in its own right: reading a schema is an HTTP round
    trip to the catalog on the path a completed query takes, so "did this need a
    lookup at all" is a property worth asserting and not just an implementation
    detail.
    """

    def __init__(self, columns_by_ref):
        self._inner = MappingSchemaLookup(columns_by_ref)
        self.calls: list[str] = []

    async def columns(self, asset):
        self.calls.append(asset.table)
        return await self._inner.columns(asset)


ORDERS = ["id", "customer_id", "amount", "quantity", "unit_price", "ts", "status"]
CUSTOMERS = ["id", "name", "first_name", "last_name", "region"]

SCHEMAS = {
    ref("orders"): ORDERS,
    ref("customers"): CUSTOMERS,
    ref("orders", catalog=RAW): ORDERS,
}


async def derive(sql: str, *, schemas=None, active_catalog: str | None = "warehouse"):
    return await columns_for_sql(
        sql,
        active_catalog=active_catalog,
        catalog_ids=CATALOG_IDS,
        schemas=schemas if schemas is not None else MappingSchemaLookup(SCHEMAS),
    )


async def pairs_for(sql: str, source: str = "orders", **kwargs) -> set[tuple[str, str]]:
    """The ``(source_column, target_column)`` set on one edge of a statement."""
    result = await derive(sql, **kwargs)
    key = (ref(source).key, ref("target").key)
    assert key in result, f"no edge from {source}; got {sorted(k[0] for k in result)}"
    assert result[key].state == DERIVED
    return {(p.source_column, p.target_column) for p in result[key].pairs}


# ── Projection, aliases, qualified references ─────────────────────────────────


async def test_simple_projection():
    assert await pairs_for("CREATE TABLE target AS SELECT id, amount FROM orders") == {
        ("id", "id"),
        ("amount", "amount"),
    }


async def test_alias_renames_the_target_column():
    got = await pairs_for("CREATE TABLE target AS SELECT amount AS total FROM orders")
    assert got == {("amount", "total")}


async def test_relation_alias_is_not_mistaken_for_the_table():
    """`o` is what the query called it; `orders` is what the graph must record."""
    got = await pairs_for("CREATE TABLE target AS SELECT o.amount FROM orders o")
    assert got == {("amount", "amount")}


async def test_fully_qualified_reference():
    sql = (
        "CREATE TABLE target AS SELECT warehouse.analytics.orders.amount "
        "FROM warehouse.analytics.orders"
    )
    assert await pairs_for(sql) == {("amount", "amount")}


async def test_same_column_name_from_different_relations_is_not_conflated():
    """`id` exists on both sides; each output must trace to the right one."""
    sql = """
        CREATE TABLE target AS
        SELECT o.id AS order_id, c.id AS customer_id
        FROM orders o JOIN customers c ON o.customer_id = c.id
    """
    result = await derive(sql)
    assert {
        (p.source_column, p.target_column)
        for p in result[(ref("orders").key, ref("target").key)].pairs
    } == {("id", "order_id")}
    assert {
        (p.source_column, p.target_column)
        for p in result[(ref("customers").key, ref("target").key)].pairs
    } == {("id", "customer_id")}


async def test_same_table_in_two_catalogs_stays_separate():
    sql = """
        CREATE TABLE target AS
        SELECT w.amount AS current, r.amount AS landed
        FROM warehouse.analytics.orders w JOIN raw.analytics.orders r ON w.id = r.id
    """
    result = await derive(sql)
    assert {
        (p.source_column, p.target_column)
        for p in result[(ref("orders").key, ref("target").key)].pairs
    } == {("amount", "current")}
    assert {
        (p.source_column, p.target_column)
        for p in result[(ref("orders", catalog=RAW).key, ref("target").key)].pairs
    } == {("amount", "landed")}


# ── Expressions ───────────────────────────────────────────────────────────────


async def test_arithmetic_gives_many_sources_one_target():
    sql = "CREATE TABLE target AS SELECT quantity * unit_price AS total FROM orders"
    assert await pairs_for(sql) == {("quantity", "total"), ("unit_price", "total")}


async def test_string_concatenation_gives_many_sources_one_target():
    sql = """
        CREATE TABLE target AS
        SELECT first_name || ' ' || last_name AS full_name FROM customers
    """
    assert await pairs_for(sql, source="customers") == {
        ("first_name", "full_name"),
        ("last_name", "full_name"),
    }


async def test_one_source_feeds_many_targets():
    sql = "CREATE TABLE target AS SELECT amount AS gross, amount AS net FROM orders"
    assert await pairs_for(sql) == {("amount", "gross"), ("amount", "net")}


async def test_coalesce_traces_every_argument():
    sql = "CREATE TABLE target AS SELECT COALESCE(amount, unit_price) AS v FROM orders"
    assert await pairs_for(sql) == {("amount", "v"), ("unit_price", "v")}


async def test_cast_is_transparent():
    sql = "CREATE TABLE target AS SELECT CAST(amount AS VARCHAR) AS v FROM orders"
    assert await pairs_for(sql) == {("amount", "v")}


async def test_case_includes_its_condition_column():
    """A CASE condition picks *which value* is returned, so it is a value dependency.

    This is the one place a predicate does contribute, and it is not an
    inconsistency with the WHERE rule below: a WHERE decides whether a row exists
    at all, while this decides what a surviving row's value is.
    """
    sql = """
        CREATE TABLE target AS
        SELECT CASE WHEN status = 'paid' THEN amount ELSE unit_price END AS v FROM orders
    """
    assert await pairs_for(sql) == {
        ("status", "v"),
        ("amount", "v"),
        ("unit_price", "v"),
    }


async def test_constants_and_functions_contribute_nothing_without_losing_the_rest():
    sql = "CREATE TABLE target AS SELECT 1 AS one, now() AS at, amount FROM orders"
    assert await pairs_for(sql) == {("amount", "amount")}


# ── Row filtering: the whole point ────────────────────────────────────────────


async def test_where_only_column_is_not_a_data_flow():
    sql = "CREATE TABLE target AS SELECT id FROM orders WHERE amount > 10"
    assert await pairs_for(sql) == {("id", "id")}


async def test_having_only_column_is_not_a_data_flow():
    sql = """
        CREATE TABLE target AS
        SELECT customer_id FROM orders GROUP BY customer_id HAVING SUM(amount) > 10
    """
    assert await pairs_for(sql) == {("customer_id", "customer_id")}


async def test_join_predicate_does_not_attribute_a_key_to_every_output():
    """The single most important negative case.

    At table level, `customers` being joined to `orders` says only that the two
    were used together. Column lineage must not turn that into a claim that
    `orders.customer_id` feeds `target.name`, which is what a naive "columns
    mentioned in the query" reading would produce.
    """
    sql = """
        CREATE TABLE target AS
        SELECT o.id, c.name
        FROM orders o JOIN customers c ON o.customer_id = c.id
    """
    result = await derive(sql)
    from_orders = {
        (p.source_column, p.target_column)
        for p in result[(ref("orders").key, ref("target").key)].pairs
    }
    from_customers = {
        (p.source_column, p.target_column)
        for p in result[(ref("customers").key, ref("target").key)].pairs
    }
    assert from_orders == {("id", "id")}
    assert from_customers == {("name", "name")}
    # Neither join key reached the other side's output.
    assert ("customer_id", "name") not in from_orders
    assert ("id", "name") not in from_customers


async def test_order_by_only_column_is_not_a_data_flow():
    sql = "CREATE TABLE target AS SELECT id FROM orders ORDER BY amount"
    assert await pairs_for(sql) == {("id", "id")}


async def test_a_source_that_contributes_nothing_is_recorded_as_such():
    """`derived` with no pairs is an answer, not an absence.

    This is what lets the UI say "nothing flows along this relationship" rather
    than "we could not tell", and the two must never be confused.
    """
    sql = """
        CREATE TABLE target AS
        SELECT o.id FROM orders o JOIN customers c ON o.customer_id = c.id
    """
    result = await derive(sql)
    customers_edge = result[(ref("customers").key, ref("target").key)]
    assert customers_edge.state == DERIVED
    assert customers_edge.pairs == ()


# ── Aggregation and windows ───────────────────────────────────────────────────


async def test_aggregate_traces_its_argument_not_the_grouping_key():
    sql = """
        CREATE TABLE target AS
        SELECT customer_id, SUM(amount) AS total FROM orders GROUP BY customer_id
    """
    assert await pairs_for(sql) == {
        ("customer_id", "customer_id"),
        ("amount", "total"),
    }


async def test_window_includes_partition_and_order_keys():
    """They change the computed value for a given row, unlike a row filter."""
    sql = """
        CREATE TABLE target AS
        SELECT SUM(amount) OVER (PARTITION BY customer_id ORDER BY ts) AS running FROM orders
    """
    assert await pairs_for(sql) == {
        ("amount", "running"),
        ("customer_id", "running"),
        ("ts", "running"),
    }


async def test_distinct_is_transparent():
    assert await pairs_for("CREATE TABLE target AS SELECT DISTINCT id FROM orders") == {
        ("id", "id")
    }


# ── CTEs, subqueries, set operations ──────────────────────────────────────────


async def test_cte_propagates_to_the_base_table():
    """The intermediate name is an implementation detail and must not be a node."""
    sql = """
        CREATE TABLE target AS
        WITH filtered AS (SELECT id, amount FROM orders WHERE amount > 0)
        SELECT id, amount * 2 AS doubled FROM filtered
    """
    result = await derive(sql)
    assert set(result) == {(ref("orders").key, ref("target").key)}
    assert await pairs_for(sql) == {("id", "id"), ("amount", "doubled")}


async def test_nested_ctes_propagate():
    sql = """
        CREATE TABLE target AS
        WITH a AS (SELECT id, amount FROM orders),
             b AS (SELECT id, amount * 2 AS scaled FROM a)
        SELECT id, scaled FROM b
    """
    assert await pairs_for(sql) == {("id", "id"), ("amount", "scaled")}


async def test_derived_table_propagates():
    sql = """
        CREATE TABLE target AS
        SELECT s.id, s.amount FROM (SELECT id, amount FROM orders) s
    """
    assert await pairs_for(sql) == {("id", "id"), ("amount", "amount")}


async def test_union_all_traces_every_branch():
    sql = """
        CREATE TABLE target AS
        SELECT o.amount FROM warehouse.analytics.orders o
        UNION ALL
        SELECT r.amount FROM raw.analytics.orders r
    """
    result = await derive(sql)
    assert {
        (p.source_column, p.target_column)
        for p in result[(ref("orders").key, ref("target").key)].pairs
    } == {("amount", "amount")}
    assert {
        (p.source_column, p.target_column)
        for p in result[(ref("orders", catalog=RAW).key, ref("target").key)].pairs
    } == {("amount", "amount")}


async def test_scalar_subquery_contributes():
    sql = """
        CREATE TABLE target AS
        SELECT o.id, (SELECT MAX(c.region) FROM customers c) AS top FROM orders o
    """
    result = await derive(sql)
    assert {
        (p.source_column, p.target_column)
        for p in result[(ref("customers").key, ref("target").key)].pairs
    } == {("region", "top")}


async def test_subquery_used_only_to_filter_contributes_nothing():
    sql = """
        CREATE TABLE target AS
        SELECT o.id FROM orders o WHERE o.customer_id IN (SELECT c.id FROM customers c)
    """
    result = await derive(sql)
    assert result[(ref("customers").key, ref("target").key)].pairs == ()


# ── SELECT * ──────────────────────────────────────────────────────────────────


async def test_star_expands_against_the_resolved_schema():
    got = await pairs_for("CREATE TABLE target AS SELECT * FROM orders")
    assert got == {(c, c) for c in ORDERS}


async def test_qualified_star_expands():
    got = await pairs_for("CREATE TABLE target AS SELECT o.* FROM orders o")
    assert got == {(c, c) for c in ORDERS}


async def test_star_over_a_subquery_only_expands_what_the_subquery_selected():
    sql = """
        CREATE TABLE target AS
        SELECT * FROM (SELECT id, amount FROM orders) s
    """
    assert await pairs_for(sql) == {("id", "id"), ("amount", "amount")}


async def test_star_across_a_join_attributes_each_column_to_its_own_table():
    """Distinct column names on both sides, so every output has one clear origin."""
    sql = """
        CREATE TABLE target AS
        SELECT o.*, c.name, c.region FROM orders o JOIN customers c ON o.customer_id = c.id
    """
    result = await derive(sql)
    from_orders = {p.source_column for p in result[(ref("orders").key, ref("target").key)].pairs}
    from_customers = {
        p.source_column for p in result[(ref("customers").key, ref("target").key)].pairs
    }
    assert from_orders == set(ORDERS)
    assert from_customers == {"name", "region"}


async def test_duplicate_output_column_names_are_refused():
    """`SELECT *` over a join sharing a column name is a trap, not a lineage case.

    DuckDB accepts it and disambiguates: joining `orders` and `customers`, which
    both have `id`, builds a table with `id` and `id_1`. Recording what the query
    text says would name a column that does not exist and would silently drop one
    of the two relationships, since only one can be called `id`. Refusing leaves
    the table edge standing and claims nothing further.
    """
    sql = """
        CREATE TABLE target AS
        SELECT * FROM orders o JOIN customers c ON o.customer_id = c.id
    """
    result = await derive(sql)
    assert result
    assert all(entry.state == UNSUPPORTED for entry in result.values())


async def test_star_without_a_resolvable_schema_yields_no_column_lineage():
    """Refusing is the point: expanding against a guess would invent columns."""
    result = await derive(
        "CREATE TABLE target AS SELECT * FROM orders",
        schemas=MappingSchemaLookup({}),
    )
    assert result[(ref("orders").key, ref("target").key)].state == UNSUPPORTED


# ── Schema lookups happen only when they must ─────────────────────────────────


async def test_qualified_join_needs_no_schema_lookup():
    lookup = CountingLookup(SCHEMAS)
    sql = """
        CREATE TABLE target AS
        SELECT o.id, c.name FROM orders o JOIN customers c ON o.customer_id = c.id
    """
    await derive(sql, schemas=lookup)
    assert lookup.calls == []


async def test_single_source_with_bare_columns_needs_no_schema_lookup():
    lookup = CountingLookup(SCHEMAS)
    await derive("CREATE TABLE target AS SELECT id, amount FROM orders", schemas=lookup)
    assert lookup.calls == []


async def test_star_needs_a_lookup_and_only_one_per_table():
    lookup = CountingLookup(SCHEMAS)
    sql = """
        CREATE TABLE target AS
        SELECT a.*, b.id AS other_id
        FROM orders a JOIN orders b ON a.id = b.id
    """
    await derive(sql, schemas=lookup)
    assert lookup.calls == ["orders"]


async def test_ambiguous_bare_column_across_two_sources_needs_a_lookup():
    lookup = CountingLookup(SCHEMAS)
    sql = """
        CREATE TABLE target AS
        SELECT name FROM orders o JOIN customers c ON o.customer_id = c.id
    """
    await derive(sql, schemas=lookup)
    assert sorted(lookup.calls) == ["customers", "orders"]


# ── Write statements ──────────────────────────────────────────────────────────


async def test_insert_with_a_column_list_names_the_targets():
    sql = "INSERT INTO target (oid, total) SELECT id, amount FROM orders"
    assert await pairs_for(sql) == {("id", "oid"), ("amount", "total")}


async def test_insert_with_a_mismatched_column_list_is_refused():
    """Position is the only thing tying the two sides together, so it must line up."""
    sql = "INSERT INTO target (oid) SELECT id, amount FROM orders"
    result = await derive(sql)
    assert result[(ref("orders").key, ref("target").key)].state == UNSUPPORTED


async def test_insert_without_a_column_list_is_refused():
    """Positional against the target's own schema is a guess we decline to make."""
    sql = "INSERT INTO target SELECT id, amount FROM orders"
    result = await derive(sql)
    assert result[(ref("orders").key, ref("target").key)].state == UNSUPPORTED


async def test_create_view_is_handled_like_a_ctas():
    sql = "CREATE VIEW target AS SELECT amount AS total FROM orders"
    assert await pairs_for(sql) == {("amount", "total")}


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE target SET status = orders.status FROM orders WHERE target.id = orders.id",
        "DELETE FROM target USING orders WHERE target.id = orders.id",
        """
        MERGE INTO target t USING orders o ON t.id = o.id
        WHEN MATCHED THEN UPDATE SET t.amount = o.amount
        """,
    ],
)
async def test_unhandled_write_statements_stay_table_level(sql):
    """`unsupported` says "not worked out", which is not the same as "nothing flows"."""
    result = await derive(sql)
    assert result
    assert all(entry.state == UNSUPPORTED for entry in result.values())


async def test_a_read_establishes_nothing():
    assert await derive("SELECT id FROM orders") == {}


async def test_unparseable_sql_yields_nothing_rather_than_raising():
    assert await derive("CREATE TABLE target AS SELECT FROM WHERE ((") == {}


async def test_unresolvable_catalog_is_dropped_not_guessed():
    sql = "CREATE TABLE target AS SELECT amount FROM nosuchcatalog.main.orders"
    assert await derive(sql) == {}


async def test_system_catalogs_are_not_lineage_sources():
    sql = "CREATE TABLE target AS SELECT column_name AS c FROM info_schema.columns"
    assert await derive(sql) == {}


# ── Multi-statement scripts ───────────────────────────────────────────────────


async def test_a_script_unions_what_each_statement_established():
    sql = """
        INSERT INTO target (a) SELECT id FROM orders;
        INSERT INTO target (b) SELECT amount FROM orders;
    """
    assert await pairs_for(sql) == {("id", "a"), ("amount", "b")}


async def test_derived_wins_over_unsupported_for_the_same_pair():
    """One statement failing to read does not retract what another established."""
    sql = """
        INSERT INTO target (a) SELECT id FROM orders;
        UPDATE target SET status = orders.status FROM orders WHERE target.a = orders.id;
    """
    result = await derive(sql)
    entry = result[(ref("orders").key, ref("target").key)]
    assert entry.state == DERIVED
    assert {(p.source_column, p.target_column) for p in entry.pairs} == {("id", "a")}


async def test_a_self_edge_is_not_recorded():
    sql = "INSERT INTO orders (id) SELECT id FROM orders"
    assert await derive(sql) == {}


# ── Caps ──────────────────────────────────────────────────────────────────────


async def test_too_many_source_tables_declines_rather_than_hammering_the_catalog():
    joins = " ".join(f"JOIN warehouse.analytics.t{i} t{i} ON t{i}.id = t0.id" for i in range(1, 14))
    sql = f"CREATE TABLE target AS SELECT * FROM warehouse.analytics.t0 t0 {joins}"
    result = await derive(sql, schemas=MappingSchemaLookup({}))
    assert result
    assert all(entry.state == UNSUPPORTED for entry in result.values())


async def test_pair_cap_declines_rather_than_storing_a_partial_answer():
    from api.services.lineage import columns as columns_module

    wide = [f"c{i}" for i in range(60)]
    lookup = MappingSchemaLookup({ref("wide"): wide})
    sql = "CREATE TABLE target AS SELECT * FROM wide"

    original = columns_module.MAX_COLUMN_PAIRS_PER_STATEMENT
    columns_module.MAX_COLUMN_PAIRS_PER_STATEMENT = 10
    try:
        result = await derive(sql, schemas=lookup)
    finally:
        columns_module.MAX_COLUMN_PAIRS_PER_STATEMENT = original

    assert result[(ref("wide").key, ref("target").key)].state == UNSUPPORTED


# ── Catalog defaults ──────────────────────────────────────────────────────────


async def test_unqualified_names_resolve_against_the_active_catalog():
    sql = "CREATE TABLE target AS SELECT amount FROM orders"
    result = await derive(sql, active_catalog="raw")
    assert (ref("orders", catalog=RAW).key, ref("target", catalog=RAW).key) in result


async def test_no_active_catalog_leaves_unqualified_names_unresolvable():
    sql = "CREATE TABLE target AS SELECT amount FROM orders"
    assert await derive(sql, active_catalog=None) == {}
