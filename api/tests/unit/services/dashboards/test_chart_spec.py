"""The chart each widget draws, inferred from its type and query."""

import uuid

import pytest

from api.services.dashboards.chart_spec import chart_spec
from api.services.dashboards.spec import DashboardSpec
from api.services.semantic.model import Metric


def metric(name, agg="sum", **presentation):
    return Metric(
        id=uuid.uuid4(),
        name=name,
        display_name=name.title(),
        description=None,
        synonyms=(),
        agg=agg,
        expr=name,
        filter=None,
        time_dimension=None,
        caveat=None,
        status="published",
        dataset="orders",
        validation_state="ok",
        **presentation,
    )


METRICS = {
    "revenue": metric("revenue", format="currency", currency="EUR", polarity="up_good"),
    "refunds": metric("refunds", format="currency", currency="EUR", polarity="down_good"),
    "orders": metric("orders", agg="count", unit="orders"),
    "aov": metric("aov", agg="avg", format="currency", currency="EUR"),
}


def widget(type_, **fields):
    data = {"id": "w", "type": type_, "title": "W", **fields}
    spec = DashboardSpec.model_validate(
        {"version": 1, "name": "d", "title": "D", "rows": [{"widgets": [data]}]}
    )
    return spec.rows[0].widgets[0]


def query(**q):
    return {"model": "orders", **q}


def test_a_kpi_shows_one_measure_with_its_metric_presentation():
    chart = chart_spec(widget("kpi", query=query(metrics=["revenue"])), METRICS)
    assert chart.type == "kpi" and chart.x is None
    assert chart.y[0].model_dump() == {
        "field": "revenue",
        "label": "Revenue",
        "format": "currency",
        "unit": None,
        "currency": "EUR",
        "polarity": "up_good",
    }


def test_a_line_plots_time_and_splits_by_its_dimension():
    chart = chart_spec(
        widget(
            "line", query=query(metrics=["revenue"], grain="month", dimensions=["customers.region"])
        ),
        METRICS,
    )
    assert chart.x.model_dump() == {"field": "month", "kind": "time", "grain": "month"}
    assert chart.series.field == "region"
    assert (chart.stack, chart.orientation) == (False, None)


@pytest.mark.parametrize(
    ("metrics", "stack"),
    [
        (["revenue"], False),  # one metric: nothing to stack
        (["revenue", "refunds"], True),  # additive, same currency: parts of one total
        (["revenue", "orders"], False),  # different units: a stack would add euros to orders
        (["revenue", "aov"], False),  # an average does not add up
    ],
)
def test_bars_stack_only_when_they_add_up_to_a_meaningful_total(metrics, stack):
    chart = chart_spec(widget("bar", query=query(metrics=metrics, dimensions=["country"])), METRICS)
    assert chart.stack is stack


def test_bars_are_ranked_horizontally_unless_ordered_by_their_category():
    ranked = chart_spec(
        widget("bar", query=query(metrics=["revenue"], dimensions=["country"])), METRICS
    )
    assert (ranked.orientation, ranked.limits.rows) == ("horizontal", 50)

    ordered = widget(
        "bar",
        query=query(
            metrics=["revenue"], dimensions=["size"], order_by=[{"field": "size"}], limit=5
        ),
    )
    chart = chart_spec(ordered, METRICS)
    assert (chart.orientation, chart.limits.rows) == ("vertical", 5)


def test_a_table_formats_its_measures_and_reads_its_row_limit():
    chart = chart_spec(
        widget("table", query=query(metrics=["revenue", "orders"], dimensions=["country"])), METRICS
    )
    assert chart.type == "table" and [m.field for m in chart.y] == ["revenue", "orders"]
    assert chart.limits.rows == 1000


def test_a_metric_the_caller_did_not_describe_is_shown_unformatted():
    chart = chart_spec(widget("kpi", query=query(metrics=["mystery"])))
    assert chart.y[0].model_dump(exclude_none=True) == {"field": "mystery", "label": "mystery"}


@pytest.mark.parametrize(
    ("type_", "columns", "expected"),
    [
        ("kpi", {"value": "n"}, {"type": "kpi", "y": ["n"], "x": None, "series": None}),
        (
            "line",
            {"x": "day", "y": ["n"], "series": "region"},
            {"type": "line", "y": ["n"], "x": ("day", "time"), "series": "region"},
        ),
        (
            "bar",
            {"x": "country", "y": ["n", "m"]},
            {"type": "bar", "y": ["n", "m"], "x": ("country", "category"), "series": None},
        ),
    ],
)
def test_an_sql_widget_draws_the_columns_it_names(type_, columns, expected):
    chart = chart_spec(widget(type_, sql="select 1", columns=columns))
    assert chart.type == expected["type"]
    assert [m.field for m in chart.y] == expected["y"]
    assert ((chart.x.field, chart.x.kind) if chart.x else None) == expected["x"]
    assert (chart.series.field if chart.series else None) == expected["series"]
    assert chart.stack is False


def test_series_keys_and_colours_are_left_for_the_run():
    chart = chart_spec(
        widget("line", query=query(metrics=["revenue"], grain="day", dimensions=["region"])),
        METRICS,
    )
    assert chart.series.keys == [] and chart.colors == {}
    assert chart.limits.series == 8
