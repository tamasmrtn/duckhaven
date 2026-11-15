"""Dashboards over MCP: the authoring guide, and results cut to fit.

An MCP client's context is the scarce resource here, not the server's time. A
dashboard run returns every widget's rows, chart and definitions; an agent
checking that a file works needs a few rows of each, what window and filters
they cover, and how the widget would draw. :func:`results` cuts a run down to
that, within a fixed budget, so a twelve-widget preview costs about as much
context as one ``run_sql`` result.

Nothing here touches the gateway or the database: the guide is text, and the
rest works on events a tool has already read.
"""

from __future__ import annotations

import json

from api.services.assistant import charts

#: Widgets one preview or run returns. A dashboard's worth, without letting one
#: call fill the context.
MAX_WIDGETS = 12
#: Rows of each widget an agent may ask for.
MAX_ROWS = 20
#: Characters a cell keeps; enough for a name or a date, never a document.
CELL_CHARS = 80
#: Bytes one widget's result may take, and one call's widgets together.
WIDGET_BYTES = 1024
CALL_BYTES = 6 * 1024

GUIDE = """\
# Writing a DuckHaven dashboard

A dashboard is one YAML file, `<name>.dashboard.yml`: a title, optional parameters, and rows of
widgets. Each widget reads a published metric from the semantic layer (`query`) or runs its own
`sql`, as the person viewing it.

## The loop

1. Find the metrics with `search_semantic` and `get_semantic_model`. Prefer a `query` over a
   metric to `sql`: the metric means the same on every dashboard and carries its own format.
2. Write the file.
3. `validate_dashboard` checks it as publishing would. Fix every error; each names a path, and
   often a hint.
4. `preview_dashboard` runs it as you and returns each widget's first rows, how it draws
   (`encoding`) and `lint`. Fix what the lint says.
5. This server saves nothing. Give the person the file: in DuckHaven they create a dashboard,
   paste the file into its editor, check it in the live preview and publish it.

## An example

```yaml
version: 1
name: sales-overview          # lowercase letters, digits, - and _
title: Sales overview
parameters:
  period:
    type: time_range
    default: last_90_days
  region:
    type: dimension
    model: orders
    dimension: region
rows:
  - widgets:
      - id: revenue           # unique in the file: lowercase letters, digits and _
        type: kpi
        title: Revenue
        width: 4              # of 12 columns; widgets without one share the rest
        query:
          model: orders
          metrics: [revenue]
          time_range: $period
          compare: previous_period
      - id: revenue_trend
        type: line
        title: Revenue by week
        query: {model: orders, metrics: [revenue], grain: week, time_range: $period}
  - widgets:
      - id: by_channel
        type: bar
        title: Revenue by channel
        query:
          model: orders
          metrics: [revenue]
          dimensions: [channel]
          time_range: $period
          limit: 12
      - id: notes
        type: text
        markdown: Revenue is net of refunds.
```

## Widgets

What each type's `query` must have:

- `kpi`: one metric, no dimensions and no grain. `compare: previous_period` or `previous_year`
  adds the number for an earlier window, and needs a `time_range`.
- `line`: a `grain` (day, week, month, quarter or year), and at most one dimension, which needs
  exactly one metric.
- `bar`: exactly one dimension, no grain, and a `limit` of at most 50. Twelve bars or fewer read
  best.
- `table`: anything, with a `limit` of at most 1,000.
- `text`: no query, but `markdown` of up to 4,000 characters, with no images or HTML. Links go
  to https:// addresses or to a DuckHaven path such as /my-workspace/catalog.

A `query` is the object `query_metric` takes: `model`, `metrics`, `dimensions`, `grain`,
`time_range`, `filters`, `order_by` and `limit`. Its `time_range` is a window, such as
`{kind: last_complete, grain: month, n: 12}`, or `$name` for a time_range parameter.

An `sql` widget takes `sql` instead: one SELECT, with tables as `catalog.schema.table`. `params`
binds its `$name` placeholders to parameters, and `columns` says what to draw:

```yaml
- id: refunds
  type: bar
  title: Refunds by region
  sql: |
    SELECT region, sum(amount) AS refunds
    FROM sales.analytics.refunds
    WHERE ($from IS NULL OR refunded_on >= $from) AND ($to IS NULL OR refunded_on < $to)
    GROUP BY region ORDER BY refunds DESC LIMIT 12
  params: {from: $period.start, to: $period.end}
  columns: {x: region, y: [refunds]}
```

`columns` is `{value: <col>}` for a kpi, `{x: <time>, y: [<col>], series: <col>}` for a line
and `{x: <col>, y: [<col>]}` for a bar; a table takes none. `$period.start` is the window's
first day and `$period.end` the day after it. Both are NULL while the window is all time, so
write filters where NULL means no filter.

## Parameters

- `time_range`: a window, which a widget uses only where its query says `time_range: $name`.
  Its `default` is a window or a preset: today, last_7_days, last_30_days, last_90_days,
  last_12_months, week_to_date, month_to_date, quarter_to_date, year_to_date, last_week,
  last_month, last_quarter or last_year.
- `dimension`: one value of one dimension of a `model`. It filters every query widget over that
  model, except a widget that lists it under `ignore`, shows that dimension itself, or cannot
  reach it. An sql widget sees it only through `params`.

## Limits

At most 8 parameters, 20 rows, 8 widgets in a row and 40 widgets in all. Keep it under 16:
every widget runs when the page opens. A row's widths add up to 12 or less. Titles are at most
120 characters.

## Rules of thumb

- A title names what is measured and holds no number: the number changes and the title would
  not.
- Lead with the one or two numbers the audience watches, then their trend, then the breakdowns.
- Measures with different units go in separate widgets, never on one axis.
- Every key must be one the schema lists; an unknown key is an error, not ignored.
- The YAML is read strictly: only true and false are booleans, there are no anchors or aliases,
  and `width: 6` is a number where `width: "6"` is refused.
- `get_dashboard_schema(part="schema")` returns the full JSON Schema.
"""

# ── Results ───────────────────────────────────────────────────────────────────


def results(events: list[dict], *, rows: int, lint: dict[str, dict] | None = None) -> dict:
    """A dashboard run's widgets as an agent reads them, at most :data:`MAX_WIDGETS`.

    Widgets come in reading order, sharing :data:`CALL_BYTES` with at most
    :data:`WIDGET_BYTES` each. ``lint`` maps a widget id to its widget, for the
    advice an author gets on what would make it read badly.
    """
    start = next((e for e in events if e.get("type") == "start"), {})
    shown = {e["id"]: e for e in events if e.get("type") == "widget"}
    order = [w for w in start.get("widgets") or shown if w in shown]
    kept = order[:MAX_WIDGETS]
    budget = min(WIDGET_BYTES, CALL_BYTES // max(len(kept), 1))
    widgets = []
    for widget_id in kept:
        result = summary(shown[widget_id], rows=rows, budget=budget)
        if lint is not None and widget_id in lint:
            result["lint"] = charts.lint(lint[widget_id], shown[widget_id])
        widgets.append(result)
    out: dict = {"widgets": widgets}
    if order[MAX_WIDGETS:]:
        out["not_shown"] = order[MAX_WIDGETS:]
    out["parameters"] = [
        {"name": p["name"], "value": p["value"], "window": (p.get("window") or {}).get("text")}
        for p in start.get("parameters", [])
    ]
    return out


def summary(event: dict, *, rows: int, budget: int) -> dict:
    """One widget's run event, cut to ``rows`` rows and ``budget`` bytes.

    The compiled SQL and the metric definitions are left out: an agent that wants
    them has ``explain_metric``, and either can be larger than the rows.
    """
    out = charts.answer(event, rows=rows)
    for key in ("sql", "definitions"):
        out.pop(key, None)
    if event.get("chart"):
        out["encoding"] = encoding(event["chart"])
    if "rows" in out:
        out["rows"] = [[_cell(value) for value in row] for row in out["rows"]]
        while out["rows"] and _size(out) > budget:
            out["rows"].pop()
            out["truncated"] = True
    return out


def encoding(chart: dict) -> str:
    """How a widget draws, in one line: ``bar: x=channel, y=revenue, horizontal``."""
    kind = chart["type"]
    measures = ", ".join(m["field"] for m in chart.get("y") or [])
    if kind == "kpi":
        return f"kpi: value={measures}"
    if kind == "table":
        return "table: every column"
    parts = [f"x={chart['x']['field']}"] if chart.get("x") else []
    parts.append(f"y={measures}")
    if chart.get("series"):
        parts.append(f"series={chart['series']['field']}")
    if chart.get("stack"):
        parts.append("stacked")
    if chart.get("orientation") == "horizontal":
        parts.append("horizontal")
    return f"{kind}: {', '.join(parts)}"


def table(columns: list[str], data: list[list], *, limit: int = MAX_ROWS) -> str:
    """The first rows as a Markdown table, within :data:`CALL_BYTES`."""
    lines = [
        "| " + " | ".join(_text(c) for c in columns) + " |",
        "|" + "---|" * len(columns),
    ]
    size = sum(len(line) + 1 for line in lines)
    for row in data[:limit]:
        line = "| " + " | ".join(_text(value) for value in row) + " |"
        size += len(line) + 1
        if size > CALL_BYTES:
            break
        lines.append(line)
    return "\n".join(lines)


def _cell(value: object) -> object:
    if isinstance(value, list | dict):
        value = json.dumps(value, default=str)
    if isinstance(value, str) and len(value) > CELL_CHARS:
        return value[: CELL_CHARS - 1] + "…"
    return value


def _text(value: object) -> str:
    """A cell as Markdown table text: on one line, with its pipes escaped."""
    cell = _cell(value)
    if cell is None:
        return ""
    return str(cell).replace("\n", " ").replace("|", "\\|")


def _size(payload: dict) -> int:
    return len(json.dumps(payload, default=str))
