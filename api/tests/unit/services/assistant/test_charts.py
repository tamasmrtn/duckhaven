"""Widgets as the model reads them, the lint, and charts drawn in the chat."""

import pytest

from api.services.assistant.charts import (
    CHART_MAX_ROWS,
    ChartRefused,
    answer,
    chat_chart,
    lint,
    result_hash,
)


def _event(**overrides) -> dict:
    event = {
        "type": "widget",
        "id": "revenue",
        "state": "ok",
        "scope": {
            "window": {"start": "2026-10-15", "end": "2026-11-14", "text": "Last 30 days: …"},
            "compare_window": None,
            "applied": ["period"],
            "skipped": [{"parameter": "country", "reason": "own_dimension"}],
        },
        "rows": {
            "columns": [
                {"name": "country", "type": "VARCHAR", "role": "dimension"},
                {"name": "revenue", "type": "DECIMAL", "role": "measure", "format": "currency"},
            ],
            "data": [["NL", "10.5"], ["DE", "8"], ["FR", "2"]],
            "truncated": False,
            "query_id": "q-1",
        },
        "chart": {"type": "bar", "y": [{"field": "revenue", "format": "currency"}]},
        "cache": "hit",
        "sql": "SELECT …",
        "warnings": [],
        "definitions": [{"metric": "revenue", "expression": "sum(amount)"}],
    }
    event.update(overrides)
    return event


def _result(rows: list[dict], **overrides) -> dict:
    result = {
        "query_id": "q-7",
        "sql": "SELECT month, revenue FROM t",
        "row_count": len(rows),
        "columns": list(rows[0]) if rows else ["month", "revenue"],
        "column_schema": None,
        "rows": rows,
        "total": len(rows),
    }
    result.update(overrides)
    return result


# ── answer ────────────────────────────────────────────────────────────────────


def test_an_answer_carries_the_numbers_and_what_they_cover():
    out = answer(_event(), rows=2)

    assert out["window"] == "Last 30 days: …"
    assert out["filtered_by"] == ["period"]
    assert out["not_filtered_by"] == [{"parameter": "country", "reason": "own_dimension"}]
    assert out["rows"] == [["NL", "10.5"], ["DE", "8"]]
    assert out["row_count"] == 3 and out["truncated"] is True
    assert out["definitions"][0]["expression"] == "sum(amount)"
    assert out["from_cache"] is True and out["query_id"] == "q-1"
    # Nothing a page needs only to draw it.
    assert "chart" not in out


def test_a_widget_that_failed_says_why_and_nothing_else():
    out = answer(_event(state="no_access", error="You don't have access."), rows=10)
    assert out["error"] == "You don't have access."
    assert "rows" not in out


# ── lint ──────────────────────────────────────────────────────────────────────


def test_a_number_in_a_title_is_called_out():
    notes = lint({"type": "bar", "title": "Revenue 2026"}, None)
    assert any("title holds a number" in n for n in notes)
    assert lint({"type": "bar", "title": "Revenue by country"}, None) == []


@pytest.mark.parametrize(
    ("widget", "data", "says"),
    [
        ({"type": "kpi", "title": "Revenue"}, [["1"], ["2"]], "one number"),
        ({"type": "bar", "title": "Revenue"}, [[str(i), i] for i in range(13)], "13 bars"),
        ({"type": "table", "title": "Revenue"}, [], "No rows"),
    ],
)
def test_a_widget_that_reads_badly_is_called_out(widget, data, says):
    rows = {**_event()["rows"], "data": data}
    assert any(says in n for n in lint(widget, _event(rows=rows)))


def test_a_line_with_one_point_is_called_out():
    event = _event(
        rows={
            "columns": [{"name": "month"}, {"name": "revenue"}],
            "data": [["2026-11-01", 1]],
            "truncated": False,
            "query_id": "q",
        },
        chart={"type": "line", "x": {"field": "month"}, "y": [{"field": "revenue"}]},
    )
    assert any("one point" in n for n in lint({"type": "line", "title": "Revenue"}, event))


def test_measures_in_different_units_on_one_axis_are_called_out():
    chart = {
        "y": [{"field": "revenue", "format": "currency"}, {"field": "rate", "format": "percent"}]
    }
    assert any("own widget" in n for n in lint({"type": "bar", "title": "x"}, _event(chart=chart)))


# ── chat_chart ────────────────────────────────────────────────────────────────


def test_a_chart_is_drawn_from_the_querys_own_rows():
    rows = [{"month": "2026-09-01", "revenue": "4"}, {"month": "2026-10-01", "revenue": "6"}]
    widget = {
        "id": "trend",
        "type": "line",
        "title": "Revenue",
        "columns": {"x": "month", "y": ["revenue"]},
    }

    drawn, event = chat_chart(widget, _result(rows))

    # A whole dashboard widget, taking the query's SQL.
    assert drawn == {**widget, "sql": "SELECT month, revenue FROM t"}
    assert event["state"] == "ok"
    assert event["rows"]["data"] == [["2026-09-01", "4"], ["2026-10-01", "6"]]
    assert event["rows"]["query_id"] == "q-7"
    assert event["chart"]["x"]["field"] == "month"
    assert [c["role"] for c in event["rows"]["columns"]] == ["time", "measure"]
    assert result_hash(event) == result_hash(chat_chart(widget, _result(rows))[1])


@pytest.mark.parametrize(
    ("widget", "result", "says"),
    [
        (
            {
                "id": "a",
                "type": "bar",
                "title": "A",
                "sql": "SELECT 1",
                "columns": {"x": "m", "y": ["r"]},
            },
            _result([{"m": 1, "r": 2}]),
            "not a query or SQL",
        ),
        ({"id": "a", "type": "text", "title": "A"}, _result([{"m": 1}]), "A chart is one of"),
        (
            {"id": "a", "type": "line", "title": "A", "columns": {"x": "month", "y": ["r"]}},
            _result([{"month": 1, "r": 2}], total=CHART_MAX_ROWS + 1, row_count=CHART_MAX_ROWS + 1),
            "more than a chart draws",
        ),
        (
            {"id": "a", "type": "bar", "title": "A", "columns": {"x": "m", "y": ["r"]}},
            _result([{"m": i, "r": i} for i in range(51)]),
            "at most 50 bars",
        ),
        (
            {"id": "a", "type": "bar", "title": "A", "columns": {"x": "country", "y": ["r"]}},
            _result([{"m": 1, "r": 2}]),
            "no column 'country'",
        ),
        ({"id": "a", "type": "kpi", "title": "A"}, _result([{"r": 2}]), "names its number"),
    ],
)
def test_a_chart_that_should_not_be_drawn_is_refused(widget, result, says):
    with pytest.raises(ChartRefused, match=says):
        chat_chart(widget, result)


def test_a_result_the_agent_cut_short_is_refused():
    rows = [{"month": 1, "r": 2}]
    widget = {"id": "a", "type": "line", "title": "A", "columns": {"x": "month", "y": ["r"]}}
    with pytest.raises(ChartRefused, match="more than a chart draws"):
        chat_chart(widget, _result(rows, total=5))
