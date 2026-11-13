"""Resolving a dashboard for one view: windows, filters, bindings and scope."""

import itertools
from datetime import date

import pytest

from api.schemas.query import TypedParameter
from api.services.dashboards.resolve import (
    IGNORED,
    NOT_BOUND,
    OTHER_MODEL,
    OWN_DIMENSION,
    UNREACHABLE,
    ParameterError,
    Skipped,
    resolve,
)
from api.services.dashboards.spec import DashboardSpec
from api.services.semantic.compile import DimensionFilter, OrderTerm
from api.services.semantic.timespec import TimeRange

TODAY = date(2026, 11, 13)


def everything(model, metric):
    return {"region", "country", "channel"}


def spec(*widgets, parameters=None):
    return DashboardSpec.model_validate(
        {
            "version": 1,
            "name": "d",
            "title": "D",
            "parameters": parameters
            if parameters is not None
            else {
                "period": {"type": "time_range", "default": "last_30_days"},
                "region": {"type": "dimension", "model": "orders", "dimension": "region"},
            },
            "rows": [{"widgets": [w]} for w in widgets],
        }
    )


def kpi(id="k", **query):
    return {
        "id": id,
        "type": "kpi",
        "title": "K",
        "query": {"model": "orders", "metrics": ["revenue"], **query},
    }


def run(dashboard, values=None, reachable=everything):
    return resolve(dashboard, values or {}, today=TODAY, reachable=reachable)


# ── Time ──────────────────────────────────────────────────────────────────────


def test_a_time_parameter_applies_where_a_widget_names_it_and_nowhere_else():
    resolved = run(spec(kpi("uses", time_range="$period"), kpi("fixed")))
    uses, fixed = resolved.widgets["uses"], resolved.widgets["fixed"]

    assert uses.window.text == "Last 30 days: 15 Oct – 13 Nov 2026"
    assert (uses.window.start, uses.window.end) == (date(2026, 10, 15), date(2026, 11, 14))
    assert uses.query.time_range == TimeRange(kind="trailing", grain="day", n=30)
    assert "period" in uses.applied
    assert fixed.window is None and fixed.query.time_range is None
    assert "period" not in fixed.applied


def test_the_viewer_can_choose_a_preset_a_window_or_all_time():
    dashboard = spec(kpi(time_range="$period"))
    month = run(dashboard, {"period": "last_month"}).widgets["k"]
    assert month.window.text == "Last month: 1 – 31 Oct 2026"

    window = {"kind": "absolute", "start": "2026-01-01", "end": "2026-04-01"}
    explicit = run(dashboard, {"period": window}).widgets["k"]
    assert explicit.window.text == "1 Jan – 31 Mar 2026"

    cleared = run(dashboard, {"period": None}).widgets["k"]
    assert cleared.window is None and cleared.query.time_range is None
    assert "period" not in cleared.applied


def test_a_widget_can_fix_its_own_window():
    window = {"kind": "last_complete", "grain": "month", "n": 6}
    resolved = run(spec(kpi(time_range=window))).widgets["k"]
    assert resolved.window.text == "Last 6 complete months: 1 May – 31 Oct 2026"


@pytest.mark.parametrize(
    ("window", "label"),
    [
        ({"kind": "to_date", "grain": "day"}, "Today: 13 Nov 2026"),
        ({"kind": "to_date", "grain": "quarter"}, "Quarter to date: 1 Oct – 13 Nov 2026"),
        ({"kind": "trailing", "grain": "week", "n": 1}, "This week: 9 – 13 Nov 2026"),
        ({"kind": "last_complete", "grain": "day", "n": 1}, "Yesterday: 12 Nov 2026"),
        ({"kind": "last_complete", "grain": "year", "n": 1}, "Last year: 1 Jan – 31 Dec 2025"),
        (
            {"kind": "trailing", "grain": "month", "n": 14},
            "Last 14 months: 1 Oct 2025 – 13 Nov 2026",
        ),
    ],
)
def test_windows_are_named_in_words_and_dates(window, label):
    assert run(spec(kpi(time_range=window))).widgets["k"].window.text == label


def test_a_kpi_comparison_resolves_its_earlier_window():
    resolved = run(
        spec(kpi(time_range="$period", compare="previous_year")), {"period": "month_to_date"}
    )
    widget = resolved.widgets["k"]
    assert widget.compare.text == "Same dates a year earlier: 1 – 13 Nov 2025"
    assert widget.query.compare == "previous_year"


def test_a_comparison_that_cannot_be_made_fails_that_widget_only():
    dashboard = spec(kpi("a", time_range="$period", compare="previous_year"), kpi("b"))
    resolved = run(dashboard, {"period": {"kind": "trailing", "grain": "month", "n": 24}})
    assert resolved.widgets["a"].query is None
    assert "longer than a year" in resolved.widgets["a"].error
    assert resolved.widgets["b"].error is None


def test_with_the_window_cleared_a_kpi_compares_nothing():
    widget = run(
        spec(kpi(time_range="$period", compare="previous_period")), {"period": None}
    ).widgets["k"]
    assert widget.compare is None and widget.query.compare is None and widget.error is None


@pytest.mark.parametrize(
    ("values", "message"),
    [
        ({"when": "last_month"}, "no parameter 'when'"),
        ({"period": "last_31_days"}, "is not a window"),
        ({"period": {"kind": "trailing"}}, "is not a valid window"),
        (
            {"period": {"kind": "absolute", "start": "2026-02-01", "end": "2026-01-01"}},
            "on or before",
        ),
        ({"region": ["EU", "US"]}, "holds one value"),
    ],
)
def test_values_that_do_not_fit_their_parameter_are_refused(values, message):
    with pytest.raises(ParameterError) as caught:
        run(spec(kpi(time_range="$period")), values)
    assert message in str(caught.value)


# ── Dimension filters ─────────────────────────────────────────────────────────


def test_a_dimension_parameter_filters_matching_widgets_with_no_yaml():
    widget = run(spec(kpi()), {"region": "EU"}).widgets["k"]
    assert widget.query.filters == (DimensionFilter(dimension="region", op="eq", values=("EU",)),)
    assert widget.applied == ("region",)


def test_the_default_value_applies_until_the_viewer_changes_it():
    params = {
        "region": {"type": "dimension", "model": "orders", "dimension": "region", "default": "EU"}
    }
    dashboard = spec(kpi(), parameters=params)
    assert run(dashboard).widgets["k"].applied == ("region",)
    assert run(dashboard, {"region": None}).widgets["k"].applied == ()


def test_a_widgets_own_filters_are_kept_alongside_the_parameters():
    own = {"dimension": "channel", "op": "in", "values": ["web", "shop"]}
    widget = run(spec(kpi(filters=[own])), {"region": "EU"}).widgets["k"]
    assert widget.query.filters == (
        DimensionFilter(dimension="channel", op="in", values=("web", "shop")),
        DimensionFilter(dimension="region", op="eq", values=("EU",)),
    )


@pytest.mark.parametrize(
    ("set_", "ignored", "same_model", "own", "reachable"),
    list(itertools.product([True, False], repeat=5)),
)
def test_each_dimension_parameter_is_applied_or_skipped_for_exactly_one_reason(
    set_, ignored, same_model, own, reachable
):
    """The whole rule space: set or unset, ignored, model, own dimension, reach."""
    widget = {
        "id": "b",
        "type": "bar",
        "title": "B",
        "query": {
            "model": "orders" if same_model else "web",
            "metrics": ["revenue"],
            "dimensions": ["region" if own else "channel"],
        },
        "ignore": ["region"] if ignored else [],
    }
    reach = everything if reachable else (lambda model, metric: {"channel"})
    resolved = run(spec(widget), {"region": "EU" if set_ else None}, reachable=reach).widgets["b"]

    if not set_:
        expected = None
    elif ignored:
        expected = IGNORED
    elif not same_model:
        expected = OTHER_MODEL
    elif own:
        expected = OWN_DIMENSION
    elif not reachable:
        expected = UNREACHABLE
    else:
        expected = "applied"

    filtered = any(f.dimension == "region" for f in resolved.query.filters)
    assert filtered == (expected == "applied")
    assert ("region" in resolved.applied) == (expected == "applied")
    assert resolved.skipped == (
        (Skipped("region", expected),) if expected not in (None, "applied") else ()
    )


def test_a_parameter_applies_only_if_every_metric_can_reach_it():
    widget = kpi(metrics=["revenue"])
    widget["type"] = "table"
    widget["query"]["metrics"] = ["revenue", "visits"]
    reach = lambda model, metric: {"region"} if metric == "revenue" else set()  # noqa: E731
    resolved = run(spec(widget), {"region": "EU"}, reachable=reach).widgets["k"]
    assert resolved.skipped == (Skipped("region", UNREACHABLE),)


def test_resolution_is_deterministic():
    dashboard = spec(kpi(time_range="$period"), kpi("b"))
    assert run(dashboard, {"region": "EU"}) == run(dashboard, {"region": "EU"})


# ── Defaults by widget type ───────────────────────────────────────────────────


def test_a_bar_reads_its_top_fifty_largest_first_unless_told_otherwise():
    bar = {
        "id": "b",
        "type": "bar",
        "title": "B",
        "query": {"model": "orders", "metrics": ["revenue", "cost"], "dimensions": ["country"]},
    }
    query = run(spec(bar)).widgets["b"].query
    assert (query.limit, query.order_by) == (50, (OrderTerm(field="revenue", descending=True),))

    bar["query"] |= {"limit": 5, "order_by": [{"field": "country"}]}
    query = run(spec(bar)).widgets["b"].query
    assert (query.limit, query.order_by) == (5, (OrderTerm(field="country"),))


def test_a_table_reads_up_to_a_thousand_rows():
    table = {
        "id": "t",
        "type": "table",
        "title": "T",
        "query": {"model": "orders", "metrics": ["revenue"]},
    }
    assert run(spec(table)).widgets["t"].query.limit == 1000


def test_text_widgets_run_nothing():
    text = {"id": "note", "type": "text", "markdown": "Hello"}
    assert "note" not in run(spec(kpi(), text)).widgets


# ── SQL widgets ───────────────────────────────────────────────────────────────


def test_an_sql_widget_takes_exactly_what_its_params_binds():
    sql = {
        "id": "s",
        "type": "table",
        "title": "S",
        "sql": "select * from t where d >= $from and d < $to and ($r is null or r = $r)",
        "params": {"from": "$period.start", "to": "$period.end", "r": "$region"},
    }
    other = {**sql, "id": "unbound", "params": {}}
    resolved = run(spec(sql, other), {"region": "EU"})

    widget = resolved.widgets["s"]
    assert widget.sql == sql["sql"]
    assert widget.sql_params == {
        "from": TypedParameter(type="DATE", value="2026-10-15"),
        "to": TypedParameter(type="DATE", value="2026-11-14"),
        "r": "EU",
    }
    assert widget.applied == ("period", "region")
    assert widget.window.text == "Last 30 days: 15 Oct – 13 Nov 2026"

    assert resolved.widgets["unbound"].skipped == (
        Skipped("period", NOT_BOUND),
        Skipped("region", NOT_BOUND),
    )


def test_an_unset_parameter_binds_null_so_the_sql_can_skip_its_filter():
    sql = {
        "id": "s",
        "type": "table",
        "title": "S",
        "sql": "select 1",
        "params": {"from": "$period.start", "r": "$region"},
    }
    widget = run(spec(sql), {"period": None}).widgets["s"]
    assert widget.sql_params == {"from": TypedParameter(type="DATE", value=None), "r": None}
    assert widget.applied == ()
