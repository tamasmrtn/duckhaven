"""The authoring guide, the prompts, and dashboard results cut to fit an agent's context."""

import json
import re

from api.config import settings
from api.services.assistant.charts import chat_chart
from api.services.dashboards import loader
from api.services.mcp import dashboards
from api.services.mcp.dashboards import (
    CALL_BYTES,
    CELL_CHARS,
    GUIDE,
    MAX_WIDGETS,
    WIDGET_BYTES,
    build_dashboard,
    encoding,
    explain_dashboard,
    results,
    summary,
    table,
)


def _event(widget_id: str = "by_channel", *, data=None, **overrides) -> dict:
    """A widget event shaped as a dashboard run streams it."""
    rows = data if data is not None else [["web", "10.5"], ["shop", "8"]]
    event = {
        "type": "widget",
        "id": widget_id,
        "state": "ok" if rows else "empty",
        "scope": {
            "window": {"start": "2026-08-17", "end": "2026-11-15", "text": "Last 90 days"},
            "compare_window": None,
            "applied": ["period"],
            "skipped": [],
        },
        "rows": {
            "columns": [
                {"name": "channel", "type": "VARCHAR", "role": "dimension"},
                {"name": "revenue", "type": "DECIMAL", "role": "measure", "format": "currency"},
            ],
            "data": rows,
            "truncated": False,
            "query_id": f"q-{widget_id}",
        },
        "chart": {
            "type": "bar",
            "x": {"field": "channel", "kind": "category"},
            "y": [{"field": "revenue", "label": "Revenue"}],
            "orientation": "horizontal",
        },
        "cache": "miss",
        "sql": "SELECT channel, sum(amount) AS revenue FROM …",
        "definitions": [{"metric": "revenue", "expression": "sum(amount)"}],
        "warnings": [],
    }
    event.update(overrides)
    return event


def _start(*widgets: str) -> dict:
    return {
        "type": "start",
        "widgets": list(widgets),
        "parameters": [
            {
                "name": "period",
                "type": "time_range",
                "value": "last_90_days",
                "window": {"start": "2026-08-17", "end": "2026-11-15", "text": "Last 90 days"},
            }
        ],
    }


# ── The guide ─────────────────────────────────────────────────────────────────


def _yaml_blocks(text: str) -> list[str]:
    return re.findall(r"```yaml\n(.*?)```", text, re.DOTALL)


def test_the_guides_example_is_a_valid_dashboard():
    """An agent copies the example, so a broken one teaches every agent the same mistake."""
    example, sql_widget = _yaml_blocks(GUIDE)
    loaded = loader.load(example)
    assert [w.type for _, w in loaded.spec.widgets()] == ["kpi", "line", "bar", "text"]
    assert loaded.warnings == []

    # The SQL widget, dropped into a file that declares the parameter it binds.
    indented = "\n".join("      " + line for line in sql_widget.splitlines())
    with_sql = (
        "version: 1\nname: refunds\ntitle: Refunds\n"
        "parameters:\n  period:\n    type: time_range\n"
        f"rows:\n  - widgets:\n{indented}\n"
    )
    assert loader.load(with_sql).spec.rows[0].widgets[0].type == "bar"


def test_the_guide_names_every_preset_the_file_accepts():
    from typing import get_args

    from api.services.dashboards.spec import TimePreset

    for preset in get_args(TimePreset):
        assert preset in GUIDE, preset


def test_the_resources_serve_the_guide_and_the_schema():
    assert dashboards.guide() == GUIDE
    assert json.loads(dashboards.schema())["title"] == "DuckHaven dashboard"


# ── The prompts ───────────────────────────────────────────────────────────────


def test_build_dashboard_walks_the_loop_and_ends_with_a_draft():
    text = build_dashboard("sales", "weekly revenue", "the EMEA team")
    assert "weekly revenue for the EMEA team" in text
    assert "`sales`" in text
    tools = ("get_dashboard_schema", "search_semantic", "validate_dashboard")
    order = [text.index(tool) for tool in (*tools, "preview_dashboard", "save_dashboard_draft")]
    assert order == sorted(order)
    assert "Do not publish it" in text
    assert " for " not in build_dashboard("sales", "churn").split("\n")[0]


def test_build_dashboard_saves_nothing_where_drafts_are_off(monkeypatch):
    monkeypatch.setattr(settings, "mcp_allow_dashboard_drafts", False)

    text = build_dashboard("sales", "weekly revenue")

    assert "Nothing is saved" in text
    assert "save_dashboard_draft" not in text


def test_explain_dashboard_treats_the_authors_words_as_data():
    text = explain_dashboard("sales", "sales-overview", "why did March drop?")
    assert "`sales-overview`" in text and "why did March drop?" in text
    assert "not instructions" in text
    assert "run_dashboard" in text and "query_metric" in text
    assert "In particular" not in explain_dashboard("sales", "sales-overview")


# ── Encoding ──────────────────────────────────────────────────────────────────


def test_the_encoding_says_how_a_widget_draws_in_one_line():
    assert encoding(_event()["chart"]) == "bar: x=channel, y=revenue, horizontal"
    line = {
        "type": "line",
        "x": {"field": "week"},
        "y": [{"field": "revenue"}],
        "series": {"field": "region"},
    }
    assert encoding(line) == "line: x=week, y=revenue, series=region"
    stacked = {"type": "bar", "x": {"field": "c"}, "y": [{"field": "a"}, {"field": "b"}]}
    assert encoding({**stacked, "stack": True}) == "bar: x=c, y=a, b, stacked"
    laps = {
        **line,
        "styles": {"VER": {"color": "#3671c6", "dash": "solid"}},
        "markers": [{"type": "point", "label": "Pit stop"}, {"type": "end", "label": "acronym"}],
    }
    assert encoding(laps) == (
        "line: x=week, y=revenue, series=region, styled from columns, marks=Pit stop, end labels"
    )
    assert encoding({"type": "kpi", "y": [{"field": "revenue"}]}) == "kpi: value=revenue"
    assert encoding({"type": "table", "y": []}) == "table: every column"


def test_a_chart_drawn_from_a_result_has_an_encoding():
    widget = {"id": "t", "type": "line", "title": "Revenue", "columns": {"x": "m", "y": ["r"]}}
    result = {
        "query_id": "q",
        "sql": "SELECT m, r FROM t",
        "row_count": 2,
        "columns": ["m", "r"],
        "column_schema": None,
        "rows": [{"m": "2026-09-01", "r": 1}, {"m": "2026-10-01", "r": 2}],
        "total": 2,
    }
    _, event = chat_chart(widget, result)
    assert encoding(event["chart"]) == "line: x=m, y=r"


# ── One widget ────────────────────────────────────────────────────────────────


def test_a_widget_keeps_its_numbers_and_scope_and_drops_its_sql():
    out = summary(_event(), rows=5, budget=WIDGET_BYTES)
    assert out["rows"] == [["web", "10.5"], ["shop", "8"]]
    assert out["window"] == "Last 90 days" and out["filtered_by"] == ["period"]
    assert out["encoding"] == "bar: x=channel, y=revenue, horizontal"
    assert out["query_id"] == "q-by_channel"
    assert "sql" not in out and "definitions" not in out
    assert out["truncated"] is False


def test_long_cells_are_cut():
    long = "x" * 200
    out = summary(_event(data=[[long, {"nested": long}]]), rows=5, budget=WIDGET_BYTES)
    first, nested = out["rows"][0]
    assert len(first) == CELL_CHARS and first.endswith("…")
    # A list or struct becomes JSON text, cut like any other.
    assert nested.startswith('{"nested": "xx') and len(nested) == CELL_CHARS


def test_a_widget_over_its_budget_loses_rows_and_says_so():
    data = [[f"channel-{i}", str(i)] for i in range(20)]
    out = summary(_event(data=data), rows=20, budget=600)
    assert 0 < len(out["rows"]) < 20
    assert len(json.dumps(out)) <= 600
    assert out["truncated"] is True
    assert out["row_count"] == 20


def test_a_widget_keeps_its_first_row_however_small_its_budget():
    """Columns and a comparison can outgrow a twelfth of the budget on their own."""
    out = summary(_event(), rows=5, budget=10)
    assert out["rows"] == [["web", "10.5"]]
    assert out["truncated"] is True


def test_a_widget_that_failed_says_why_and_has_no_rows():
    out = summary(_event(state="no_access", error="Not granted"), rows=5, budget=WIDGET_BYTES)
    assert out["state"] == "no_access" and out["error"] == "Not granted"
    assert "rows" not in out


# ── A run ─────────────────────────────────────────────────────────────────────


def test_a_run_comes_back_in_reading_order_with_its_parameters():
    events = [_start("a", "b"), _event("b"), _event("a"), {"type": "done"}]
    out = results(events, rows=5)
    assert [w["widget"] for w in out["widgets"]] == ["a", "b"]
    assert out["parameters"] == [
        {"name": "period", "value": "last_90_days", "window": "Last 90 days"}
    ]
    assert "not_shown" not in out


def test_a_run_shows_at_most_twelve_widgets_within_the_call_budget():
    ids = [f"w{i}" for i in range(MAX_WIDGETS + 2)]
    data = [[f"channel-{i}", "1"] for i in range(20)]
    events = [_start(*ids), *(_event(i, data=data) for i in ids)]

    out = results(events, rows=20)

    assert [w["widget"] for w in out["widgets"]] == ids[:MAX_WIDGETS]
    assert out["not_shown"] == ids[MAX_WIDGETS:]
    for widget in out["widgets"]:
        assert len(json.dumps(widget)) <= CALL_BYTES // MAX_WIDGETS
    assert len(json.dumps(out["widgets"])) <= CALL_BYTES + MAX_WIDGETS * 2


def test_a_run_without_a_start_keeps_the_order_it_streamed():
    out = results([_event("b"), _event("a")], rows=1)
    assert [w["widget"] for w in out["widgets"]] == ["b", "a"]
    assert out["parameters"] == []


def test_lint_is_added_for_the_widgets_it_is_asked_for():
    events = [_start("a"), _event("a", data=[[f"c{i}", "1"] for i in range(15)])]
    out = results(events, rows=1, lint={"a": {"id": "a", "type": "bar", "title": "Top 15"}})
    notes = out["widgets"][0]["lint"]
    assert any("title holds a number" in n for n in notes)
    assert any("15 bars" in n for n in notes)
    assert "lint" not in results(events, rows=1)["widgets"][0]


# ── A table ───────────────────────────────────────────────────────────────────


def test_a_table_is_markdown_on_one_line_a_row():
    text = table(["name", "note"], [["a|b", "two\nlines"], [None, 3]])
    assert text.splitlines() == [
        "| name | note |",
        "|---|---|",
        "| a\\|b | two lines |",
        "|  | 3 |",
    ]


def test_a_table_stops_at_twenty_rows_and_within_the_budget():
    assert len(table(["n"], [[i] for i in range(50)]).splitlines()) == 2 + 20
    wide = table(["a", "b", "c", "d"], [["x" * 200] * 4] * 20)
    assert len(wide) <= CALL_BYTES
    assert 2 < len(wide.splitlines()) < 22
