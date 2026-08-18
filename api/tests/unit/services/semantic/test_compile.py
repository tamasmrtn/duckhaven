"""What the compiler emits, asserted as structure rather than as text.

Comparisons go through ``sqlglot`` so a whitespace or pretty-printing change does
not fail a test that is really about semantics. Where a test does look at text it
is looking for one specific token — ``FILTER``, ``LEFT JOIN`` — because that token
*is* the behaviour under test.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date

import pytest
import sqlglot

from api.services.semantic.compile import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    DimensionFilter,
    MetricQuery,
    OrderTerm,
    compile_metric_query,
    legal_dimensions,
)
from api.services.semantic.errors import SemanticError
from api.services.semantic.timespec import TimeRange
from tests.unit.services.semantic.conftest import (
    make_dataset,
    make_dimension,
    make_metric,
    make_model,
    make_relationship,
)

TODAY = date(2026, 8, 17)


def compiled(model, **kwargs) -> str:
    return compile_metric_query(model, MetricQuery(**kwargs), today=TODAY).sql


def equivalent(actual: str, expected: str) -> bool:
    return sqlglot.parse_one(actual, read="duckdb") == sqlglot.parse_one(expected, read="duckdb")


def test_a_bare_metric_compiles_to_its_definition(star):
    sql = compiled(star, metrics=("revenue",))

    assert equivalent(
        sql,
        """
        SELECT SUM(orders.total_amount) FILTER(WHERE orders.status <> 'test') AS revenue
        FROM warehouse.analytics.orders AS orders
        LIMIT 500
        """,
    )


def test_the_model_never_supplies_the_aggregation(star):
    """The point of the whole subsystem, stated as a test.

    ``revenue`` is a sum over ``total_amount`` with a filter. Nothing a caller can
    pass changes that, because the caller only ever names the metric.
    """
    sql = compiled(star, metrics=("revenue",))

    assert "SUM(orders.total_amount)" in sql
    assert "FILTER" in sql
    # The filter is part of the metric, not an optional extra a caller can drop.
    assert "orders.status <> 'test'" in sql


def test_count_counts_rows_and_count_distinct_counts_values(star):
    assert "COUNT(*)" in compiled(star, metrics=("order_count",))
    assert "COUNT(DISTINCT orders.customer_id)" in compiled(star, metrics=("unique_customers",))


def test_a_dimension_on_another_dataset_brings_its_join(star):
    sql = compiled(star, metrics=("revenue",), dimensions=("country",))

    assert equivalent(
        sql,
        """
        SELECT customers.country AS country,
               SUM(orders.total_amount) FILTER(WHERE orders.status <> 'test') AS revenue
        FROM warehouse.analytics.orders AS orders
        LEFT JOIN warehouse.analytics.customers AS customers
          ON orders.customer_id = customers.id
        GROUP BY customers.country
        LIMIT 500
        """,
    )


def test_joins_are_left_joins(star):
    """An INNER join here would drop fact rows with a missing lookup.

    That turns a data-quality problem into a quietly smaller total, which is the
    kind of wrong answer nobody notices.
    """
    sql = compiled(star, metrics=("revenue",), dimensions=("country",))

    assert "LEFT JOIN" in sql
    assert "INNER JOIN" not in sql


def test_two_dimensions_on_the_same_dataset_join_it_once(star):
    sql = compiled(star, metrics=("revenue",), dimensions=("country", "segment"))

    assert sql.count("LEFT JOIN") == 1


def test_two_dimensions_on_different_datasets_join_both(star):
    sql = compiled(star, metrics=("revenue",), dimensions=("country", "category"))

    assert sql.count("LEFT JOIN") == 2


def test_names_are_fully_qualified(star):
    """So the compiled statement meets the same grant check as hand-written SQL."""
    sql = compiled(star, metrics=("revenue",), dimensions=("country",))

    assert "warehouse.analytics.orders" in sql
    assert "warehouse.analytics.customers" in sql


def test_grain_truncates_the_bound_time_dimension(star):
    sql = compiled(star, metrics=("revenue",), grain="month")

    assert "DATE_TRUNC('MONTH', orders.order_date)" in sql
    # Not the other timestamp on the same table.
    assert "created_at" not in sql


def test_several_metrics_compose_with_their_own_filters(star):
    sql = compiled(star, metrics=("revenue", "order_count"))

    assert "FILTER(WHERE" in sql
    assert "COUNT(*) AS order_count" in sql
    # One scan, not a subquery per metric.
    assert sql.count("FROM") == 1


def test_bare_columns_in_an_expression_are_qualified(star):
    """An unqualified column starts reading the wrong table the day a join lands."""
    model = make_model(
        datasets=[make_dataset("orders"), make_dataset("customers", primary_key=("id",))],
        dimensions=[make_dimension("country", "customers")],
        metrics=[make_metric("revenue", "orders", expr="total_amount", time_dimension=None)],
        relationships=[make_relationship("r", "orders", "customers")],
    )

    sql = compile_metric_query(
        model, MetricQuery(metrics=("revenue",), dimensions=("country",)), today=TODAY
    ).sql

    assert "SUM(orders.total_amount)" in sql


def test_filter_values_are_bound_as_literals(star):
    sql = compiled(
        star,
        metrics=("revenue",),
        filters=(DimensionFilter(dimension="country", op="eq", values=("United States",)),),
    )

    assert "customers.country = 'United States'" in sql


def test_a_filter_value_cannot_become_syntax(star):
    """The injection case. A value with SQL in it stays a value.

    Asserted against the parsed tree rather than the text, because the text
    *does* contain the hostile characters — escaped, inside one string literal,
    which is exactly what safety looks like here. Reading it back proves the
    predicate is a single equality against that literal and not a second clause.
    """
    hostile = "US' OR 1=1 --"
    sql = compiled(
        star,
        metrics=("revenue",),
        filters=(DimensionFilter(dimension="country", op="eq", values=(hostile,)),),
    )

    statements = sqlglot.parse(sql, read="duckdb")
    assert len(statements) == 1

    where = statements[0].find(sqlglot.exp.Where)
    predicate = where.this
    assert isinstance(predicate, sqlglot.exp.EQ)
    assert predicate.expression.this == hostile


def test_contains_escapes_wildcards_in_the_value(star):
    """A value containing % must not silently widen its own match."""
    sql = compiled(
        star,
        metrics=("revenue",),
        filters=(DimensionFilter(dimension="country", op="contains", values=("100%",)),),
    )

    assert r"%100\%%" in sql
    # Escaping without declaring the escape character is what makes the pattern
    # match nothing, so the clause is part of the contract.
    assert r"ESCAPE '\'" in sql


@pytest.mark.parametrize(
    ("value", "matches", "misses"),
    [
        ("new_york", "new_york", "newXyork"),
        ("100%", "a 100% b", "100 of them"),
        ("plain", "a plain word", "nothing here"),
    ],
)
def test_contains_matches_the_literal_value_in_duckdb(star, value, matches, misses):
    """The escaping has to work in the engine, not merely look right.

    Asserting on the generated string is what let this ship broken: the pattern
    read correctly and matched nothing, because DuckDB has no default escape
    character. This runs the predicate.
    """
    duckdb = pytest.importorskip("duckdb")
    sql = compiled(
        star,
        metrics=("revenue",),
        filters=(DimensionFilter(dimension="country", op="contains", values=(value,)),),
    )
    predicate = sqlglot.parse_one(sql, read="duckdb").find(sqlglot.exp.Where).this
    # Re-target the column at a literal so the predicate can be evaluated alone.
    rendered = predicate.sql(dialect="duckdb").replace("customers.country", "?", 1)

    conn = duckdb.connect()
    assert conn.execute(f"SELECT {rendered}", [matches]).fetchone()[0] is True
    assert conn.execute(f"SELECT {rendered}", [misses]).fetchone()[0] is False


def test_in_and_not_in(star):
    sql = compiled(
        star,
        metrics=("revenue",),
        filters=(DimensionFilter(dimension="country", op="in", values=("US", "CA")),),
    )
    assert "IN ('US', 'CA')" in sql

    negated = compiled(
        star,
        metrics=("revenue",),
        filters=(DimensionFilter(dimension="country", op="not_in", values=("US",)),),
    )
    assert "NOT" in negated


def test_null_checks_need_no_value(star):
    sql = compiled(
        star,
        metrics=("revenue",),
        filters=(DimensionFilter(dimension="country", op="is_null"),),
    )

    assert "IS NULL" in sql


def test_order_by_must_name_something_in_the_result(star):
    sql = compiled(
        star,
        metrics=("revenue",),
        dimensions=("country",),
        order_by=(OrderTerm(field="revenue", descending=True),),
    )
    assert "ORDER BY" in sql and "DESC" in sql

    with pytest.raises(SemanticError, match="not in the result"):
        compiled(star, metrics=("revenue",), order_by=(OrderTerm(field="profit"),))


def test_there_is_always_a_limit(star):
    assert f"LIMIT {DEFAULT_LIMIT}" in compiled(star, metrics=("revenue",))


def test_an_oversized_limit_is_capped_and_said_so(star):
    result = compile_metric_query(
        star, MetricQuery(metrics=("revenue",), limit=999_999), today=TODAY
    )

    assert f"LIMIT {MAX_LIMIT}" in result.sql
    assert any("reduced" in w for w in result.warnings)


def test_definitions_used_names_what_produced_the_answer(star):
    result = compile_metric_query(
        star,
        MetricQuery(metrics=("revenue",), dimensions=("country",), grain="month"),
        today=TODAY,
    )

    kinds = {(d["kind"], d["name"]) for d in result.definitions_used}
    assert ("metric", "revenue") in kinds
    assert ("dimension", "country") in kinds
    metric = next(d for d in result.definitions_used if d["kind"] == "metric")
    assert metric["expression"] == "SUM(total_amount) FILTER (WHERE status <> 'test')"


def test_a_caveat_travels_with_the_number(star):
    result = compile_metric_query(star, MetricQuery(metrics=("revenue",)), today=TODAY)

    assert any("Excludes internal test orders" in w for w in result.warnings)


def test_an_unknown_metric_lists_the_real_ones(star):
    with pytest.raises(SemanticError) as excinfo:
        compiled(star, metrics=("profit",))

    message = str(excinfo.value)
    assert "profit" in message
    assert "revenue" in message


def test_an_unknown_dimension_lists_the_real_ones(star):
    with pytest.raises(SemanticError) as excinfo:
        compiled(star, metrics=("revenue",), dimensions=("region",))

    assert "country" in str(excinfo.value)


def test_metrics_on_different_datasets_are_refused(star):
    """Two grains in one SELECT is a wrong number, not a smaller result."""
    model = make_model(
        datasets=[make_dataset("orders"), make_dataset("sessions")],
        dimensions=[],
        metrics=[
            make_metric("revenue", "orders", time_dimension=None),
            make_metric("sessions", "sessions", agg="count", expr=None, time_dimension=None),
        ],
    )

    with pytest.raises(SemanticError, match="different datasets"):
        compile_metric_query(model, MetricQuery(metrics=("revenue", "sessions")), today=TODAY)


def test_a_deprecated_metric_is_refused(star):
    model = make_model(
        datasets=[make_dataset("orders")],
        metrics=[
            make_metric(
                "revenue",
                "orders",
                status="deprecated",
                time_dimension=None,
                description="Use net_revenue instead.",
            )
        ],
    )

    with pytest.raises(SemanticError, match="deprecated"):
        compile_metric_query(model, MetricQuery(metrics=("revenue",)), today=TODAY)


def test_legal_dimensions_are_the_reachable_ones(star):
    assert legal_dimensions(star, "revenue") == [
        "category",
        "country",
        "created_at",
        "order_date",
        "segment",
    ]


def test_legal_dimensions_exclude_unreachable_datasets():
    model = make_model(
        datasets=[make_dataset("orders"), make_dataset("marketing")],
        dimensions=[
            make_dimension("channel", "marketing"),
            make_dimension("order_date", "orders", kind="time", is_default_time=True),
        ],
        metrics=[make_metric("revenue", "orders")],
    )

    assert legal_dimensions(model, "revenue") == ["order_date"]


def test_an_expression_containing_a_subquery_is_refused():
    model = make_model(
        datasets=[make_dataset("orders")],
        metrics=[
            make_metric(
                "sneaky",
                "orders",
                expr="(SELECT secret FROM other_table)",
                time_dimension=None,
            )
        ],
    )

    with pytest.raises(SemanticError, match="subquery|scalar expression"):
        compile_metric_query(model, MetricQuery(metrics=("sneaky",)), today=TODAY)


def test_an_unparseable_expression_fails_where_someone_can_fix_it():
    model = make_model(
        datasets=[make_dataset("orders")],
        metrics=[make_metric("broken", "orders", expr="SUM(((", time_dimension=None)],
    )

    with pytest.raises(SemanticError, match="not valid SQL"):
        compile_metric_query(model, MetricQuery(metrics=("broken",)), today=TODAY)


def test_no_metric_at_all_is_an_error_not_an_empty_query(star):
    with pytest.raises(SemanticError, match="No metric"):
        compiled(star, metrics=())


def test_a_time_window_bounds_the_bound_column(star):
    sql = compiled(
        star,
        metrics=("revenue",),
        time_range=TimeRange(kind="last_complete", grain="month", n=1),
    )

    assert "orders.order_date >= CAST('2026-07-01' AS DATE)" in sql
    assert "orders.order_date < CAST('2026-08-01' AS DATE)" in sql


def test_a_broken_metric_says_it_is_broken_rather_than_missing():
    """The two are not the same, and they call for opposite responses.

    "There is no revenue metric" invites the assistant to work one out. "Revenue
    exists but is broken" tells it to stop and say so — which is the whole point
    of withholding a definition whose bindings no longer resolve.
    """
    model = make_model(
        datasets=[make_dataset("orders")],
        metrics=[make_metric("revenue", "orders", time_dimension=None)],
    )
    # As the loader leaves it once validation has failed: absent from `metrics`,
    # remembered in `broken_metrics`.
    broken = replace(
        model,
        metrics={},
        broken_metrics={"revenue": "Column total_amount no longer exists."},
    )

    with pytest.raises(SemanticError) as excinfo:
        compile_metric_query(broken, MetricQuery(metrics=("revenue",)), today=TODAY)

    message = str(excinfo.value)
    assert "currently broken" in message
    assert "has no metric" not in message
    assert "Do not substitute your own calculation" in message


def test_a_genuinely_unknown_metric_still_reports_as_unknown(star):
    with pytest.raises(SemanticError) as excinfo:
        compiled(star, metrics=("profit",))

    assert "has no metric" in str(excinfo.value)


def test_a_broken_dimension_says_so_too():
    model = make_model(
        datasets=[make_dataset("orders")],
        metrics=[make_metric("revenue", "orders", time_dimension=None)],
    )
    broken = replace(model, broken_dimensions={"country": "Column country no longer exists."})

    with pytest.raises(SemanticError, match="currently broken"):
        compile_metric_query(
            broken, MetricQuery(metrics=("revenue",), dimensions=("country",)), today=TODAY
        )
