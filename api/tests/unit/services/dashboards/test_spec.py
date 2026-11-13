"""The dashboard format's shape and reference rules, on parsed documents."""

import copy

import pytest
from pydantic import ValidationError

from api.services.dashboards.spec import (
    MAX_WIDGETS,
    WIDGET_WARNING,
    DashboardSpec,
    check,
    markdown_problem,
)

BASE = {
    "version": 1,
    "name": "sales-overview",
    "title": "Sales",
    "parameters": {
        "period": {"type": "time_range", "default": "last_30_days"},
        "region": {"type": "dimension", "model": "orders", "dimension": "region"},
    },
    "rows": [
        {
            "widgets": [
                {
                    "id": "revenue",
                    "type": "kpi",
                    "title": "Revenue",
                    "query": {
                        "model": "orders",
                        "metrics": ["revenue"],
                        "time_range": "$period",
                        "compare": "previous_period",
                    },
                }
            ]
        }
    ],
}


def doc(widget=None, **top):
    out = copy.deepcopy(BASE)
    if widget is not None:
        out["rows"] = [{"widgets": [widget]}]
    out.update(top)
    return out


def errors(data) -> list[str]:
    with pytest.raises(ValidationError) as caught:
        DashboardSpec.model_validate(data)
    return [e["msg"] for e in caught.value.errors()]


def kpi(**query):
    return {
        "id": "w",
        "type": "kpi",
        "title": "W",
        "query": {"model": "orders", "metrics": ["revenue"], **query},
    }


def test_the_example_is_valid_and_clean():
    spec = DashboardSpec.model_validate(BASE)
    assert check(spec) == []


@pytest.mark.parametrize(
    ("where", "value"),
    [
        ("top", {"colour": "red"}),
        ("widget", {"colour": "red"}),
        ("query", {"metric": ["revenue"]}),
        ("time_range", {"grains": "day"}),
        ("filter", {"value": "x"}),
    ],
)
def test_an_unknown_key_anywhere_is_refused_not_ignored(where, value):
    data = copy.deepcopy(BASE)
    widget = data["rows"][0]["widgets"][0]
    target = {
        "top": data,
        "widget": widget,
        "query": widget["query"],
    }.get(where)
    if where == "time_range":
        widget["query"]["time_range"] = {"kind": "trailing", "grain": "day", "n": 7, **value}
    elif where == "filter":
        widget["query"]["filters"] = [
            {"dimension": "region", "op": "eq", "values": ["EU"], **value}
        ]
    else:
        target.update(value)
    assert any("Extra inputs" in m for m in errors(data))


@pytest.mark.parametrize(
    ("field", "value"),
    [("width", "6"), ("width", 6.0), ("width", True), ("title", 5)],
)
def test_types_are_strict(field, value):
    assert errors(doc({**kpi(), field: value}))


def test_dates_are_read_from_text_so_json_bodies_work():
    window = {"kind": "absolute", "start": "2026-01-01", "end": "2026-02-01"}
    DashboardSpec.model_validate(doc(kpi(time_range=window)))


def test_an_absolute_window_must_end_after_it_starts():
    window = {"kind": "absolute", "start": "2026-02-01", "end": "2026-02-01"}
    assert any("on or before it starts" in m for m in errors(doc(kpi(time_range=window))))


@pytest.mark.parametrize(
    ("widget", "message"),
    [
        (kpi(metrics=["a", "b"]), "one metric"),
        (kpi(dimensions=["region"]), "no dimensions"),
        (kpi(grain="month"), "no grain"),
        (kpi(compare="previous_year"), "compare needs a time_range"),
        (
            {"id": "w", "type": "line", "title": "W", "query": {"model": "o", "metrics": ["a"]}},
            "needs a grain",
        ),
        (
            {
                "id": "w",
                "type": "line",
                "title": "W",
                "query": {"model": "o", "metrics": ["a", "b"], "grain": "day", "dimensions": ["r"]},
            },
            "only when it plots one metric",
        ),
        (
            {
                "id": "w",
                "type": "line",
                "title": "W",
                "query": {
                    "model": "o",
                    "metrics": ["a"],
                    "grain": "day",
                    "time_range": "$period",
                    "compare": "previous_period",
                },
            },
            "kpi widgets only",
        ),
        (
            {
                "id": "w",
                "type": "bar",
                "title": "W",
                "query": {"model": "o", "metrics": ["a"], "dimensions": ["r"], "grain": "day"},
            },
            "takes no grain",
        ),
        (
            {"id": "w", "type": "bar", "title": "W", "query": {"model": "o", "metrics": ["a"]}},
            "exactly one dimension",
        ),
        (
            {
                "id": "w",
                "type": "bar",
                "title": "W",
                "query": {"model": "o", "metrics": ["a"], "dimensions": ["r"], "limit": 51},
            },
            "at most 50 bars",
        ),
        (
            {
                "id": "w",
                "type": "table",
                "title": "W",
                "query": {"model": "o", "metrics": ["a"], "limit": 1001},
            },
            "at most 1000 rows",
        ),
    ],
)
def test_each_widget_type_takes_only_the_query_shapes_it_can_draw(widget, message):
    assert any(message in m for m in errors(doc(widget)))


def test_a_widget_reads_from_exactly_one_source():
    both = {**kpi(), "sql": "select 1 as n", "columns": {"value": "n"}}
    neither = {"id": "w", "type": "kpi", "title": "W"}
    for widget in (both, neither):
        assert any("exactly one of query" in m for m in errors(doc(widget)))


@pytest.mark.parametrize(
    ("widget", "message"),
    [
        ({**kpi(), "columns": {"value": "n"}}, "columns is for SQL widgets"),
        ({**kpi(), "params": {"s": "$period.start"}}, "params binds SQL placeholders"),
        (
            {"id": "w", "type": "table", "title": "W", "sql": "select 1", "ignore": ["region"]},
            "ignore is for query widgets",
        ),
        ({"id": "w", "type": "kpi", "title": "W", "sql": "select 1"}, "names its number"),
        ({"id": "w", "type": "line", "title": "W", "sql": "select 1"}, "names its columns"),
        ({"id": "w", "type": "bar", "title": "W", "sql": "select 1"}, "names its columns"),
        (
            {
                "id": "w",
                "type": "line",
                "title": "W",
                "sql": "select 1",
                "columns": {"x": "d", "y": ["a", "b"], "series": "r"},
            },
            "only when it plots one y column",
        ),
    ],
)
def test_sql_and_query_widgets_keep_to_their_own_keys(widget, message):
    assert any(message in m for m in errors(doc(widget)))


def test_a_row_fits_twelve_columns_counting_one_for_each_unsized_widget():
    def row(*widths):
        return [
            {**kpi(), "id": f"w{i}", **({"width": w} if w else {})} for i, w in enumerate(widths)
        ]

    DashboardSpec.model_validate({**BASE, "rows": [{"widgets": row(6, 6)}]})
    DashboardSpec.model_validate({**BASE, "rows": [{"widgets": row(10, None, None)}]})
    assert any(
        "add up to 12 of 12" in m for m in errors({**BASE, "rows": [{"widgets": row(6, 6, None)}]})
    )
    assert errors({**BASE, "rows": [{"widgets": row(*[None] * 9)}]})  # nine widgets in one row


def test_a_dimension_parameter_holds_one_value():
    params = {"region": {**BASE["parameters"]["region"], "multiple": True}}
    assert any("holds one value" in m for m in errors(doc(parameters=params)))


def test_a_time_parameter_defaults_to_a_preset_or_a_window():
    for default in ("year_to_date", {"kind": "last_complete", "grain": "month", "n": 3}):
        DashboardSpec.model_validate(
            doc(parameters={"period": {"type": "time_range", "default": default}})
        )
    bad = {"period": {"type": "time_range", "default": "last_31_days"}}
    assert errors(doc(parameters=bad))


def _problems(data):
    return [(p.path, p.message, p.warning) for p in check(DashboardSpec.model_validate(data))]


def test_a_reference_must_name_a_declared_parameter_of_the_right_kind():
    wrong_kind = doc(kpi(time_range="$region"))
    missing = doc(kpi(time_range="$when"))
    ignore = doc({**kpi(), "ignore": ["period"]})
    sql = doc(
        {
            "id": "w",
            "type": "table",
            "title": "W",
            "sql": "select 1",
            "params": {"a": "$period", "b": "$region.start", "c": "$nope"},
        }
    )
    assert (
        "rows[0].widgets[0].query.time_range",
        "$region is a dimension parameter; this needs a time_range parameter.",
        False,
    ) in _problems(wrong_kind)
    assert any("declared: $period, $region" in m for _, m, _ in _problems(missing))
    assert any(p == "rows[0].widgets[0].ignore[0]" for p, _, _ in _problems(ignore))
    found = {p: m for p, m, _ in _problems(sql)}
    assert "bind $period.start or $period.end" in found["rows[0].widgets[0].params.a"]
    assert "without .start" in found["rows[0].widgets[0].params.b"]
    assert "not a parameter" in found["rows[0].widgets[0].params.c"]


def test_widget_ids_are_unique():
    data = {**BASE, "rows": [{"widgets": [kpi(), kpi()]}]}
    assert (
        "rows[0].widgets[1].id",
        "Widget id 'w' is already used at rows[0].widgets[0].",
        False,
    ) in _problems(data)


def test_unused_parameters_are_warnings_not_errors():
    data = doc(
        kpi(),
        parameters={
            **BASE["parameters"],
            "shop": {"type": "dimension", "model": "web", "dimension": "shop"},
        },
    )
    problems = _problems(data)
    assert (
        "parameters.period",
        "No widget uses $period; write time_range: $period in a widget's query.",
        True,
    ) in problems
    assert (
        "parameters.shop",
        "No widget reads model 'web', so $shop filters nothing.",
        True,
    ) in problems
    # region is used implicitly by every query widget over orders.
    assert not any(p == "parameters.region" for p, _, _ in problems)


def test_widget_count_warns_past_sixteen_and_stops_at_forty():
    def rows(n):
        widgets = [{**kpi(time_range="$period"), "id": f"w{i}", "width": 1} for i in range(n)]
        return [{"widgets": widgets[i : i + 8]} for i in range(0, n, 8)]

    assert _problems({**BASE, "rows": rows(WIDGET_WARNING)}) == []
    assert [w for _, _, w in _problems({**BASE, "rows": rows(WIDGET_WARNING + 1)})] == [True]
    assert [w for _, _, w in _problems({**BASE, "rows": rows(MAX_WIDGETS + 1)})] == [False]


@pytest.mark.parametrize(
    "text",
    [
        "# Notes\nSee [the guide](https://example.com/guide) and [catalog](/acme/catalog).",
        "Visit <https://example.com>.",
        "[ref]: https://example.com\nUse [ref].",
        "Code `![not an image](x)` and ```\n<div>html in a fence</div>\n``` are fine.",
        "Bare https://example.com links are fine.",
    ],
)
def test_markdown_allows_https_and_duckhaven_links(text):
    assert markdown_problem(text) is None


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("![chart](https://example.com/c.png)", "no images"),
        ("<img src=x>", "not HTML"),
        ("<script>alert(1)</script>", "not HTML"),
        ("[x](http://example.com)", "refused"),
        ("[x](javascript:alert(1))", "refused"),
        ("[x](//evil.example)", "refused"),
        ("<mailto:a@b.c>", "refused"),
        ("[ref]: data:text/html,hi", "refused"),
        ("go to www.example.com", "https://"),
        ("plain http://example.com", "https://"),
    ],
)
def test_markdown_refuses_images_html_and_other_links(text, message):
    assert message in markdown_problem(text)
