"""Dashboard widgets as the assistant sees them, and charts it draws in the chat.

A widget's run event carries everything a page needs to draw it. The model needs
less: the numbers, what window and filters they cover, and what the metrics mean.
:func:`answer` cuts an event down to that.

A chart in the chat is a dashboard widget too. The model names a query it ran and
how to draw it; the rows come from that query, never from the model, and are
drawn by the same code a dashboard view uses, so the chart is what the person
would see on a dashboard and can later be added to one as it stands.
"""

from __future__ import annotations

import hashlib
import json
import re

from pydantic import ValidationError

from api.schemas.query import RowsPageOut
from api.services.dashboards.chart_spec import chart_spec
from api.services.dashboards.run import drawn
from api.services.dashboards.spec import BAR_LIMIT, TABLE_LIMIT, DashboardSpec

#: The most rows a chart in the chat draws. A larger result is refused, not cut:
#: a chart of the first thousand rows of something bigger draws the wrong picture.
CHART_MAX_ROWS = TABLE_LIMIT
#: The widget types a chart in the chat can take; text has no data to draw.
CHART_TYPES = ("kpi", "line", "bar", "table")
#: Widgets one preview call runs: with a turn's 20 model requests, enough to try
#: out a dashboard's worth without spending the turn on it.
MAX_PREVIEW_WIDGETS = 8
#: Rows of each previewed widget the model sees.
PREVIEW_ROWS = 20
#: Bars a reader can still compare one against another.
READABLE_BARS = 12

_DIGIT = re.compile(r"\d")


class ChartRefused(ValueError):
    """A chart that should not be drawn, with what to do instead."""


def answer(event: dict, *, rows: int) -> dict:
    """One widget's run event as the model reads it: its numbers and their scope."""
    out: dict = {"widget": event["id"], "state": event["state"]}
    scope = event.get("scope") or {}
    if scope.get("window"):
        out["window"] = scope["window"]["text"]
    if scope.get("compare_window"):
        out["compare_window"] = scope["compare_window"]["text"]
    if scope.get("applied"):
        out["filtered_by"] = scope["applied"]
    if scope.get("skipped"):
        out["not_filtered_by"] = scope["skipped"]
    if event["state"] in ("error", "no_access"):
        out["error"] = event.get("error")
        return out
    data = event["rows"]["data"]
    out["columns"] = [
        {key: value for key, value in column.items() if value is not None}
        for column in event["rows"]["columns"]
    ]
    out["rows"] = data[:rows]
    out["row_count"] = len(data)
    out["truncated"] = bool(event["rows"]["truncated"]) or len(data) > rows
    for key in ("compare", "definitions", "warnings", "sql"):
        if event.get(key):
            out[key] = event[key]
    out["query_id"] = event["rows"]["query_id"]
    out["from_cache"] = event.get("cache") == "hit"
    return out


def lint(widget: dict, event: dict | None) -> list[str]:
    """What would make this widget read badly, as advice for whoever wrote it."""
    notes: list[str] = []
    if _DIGIT.search(widget.get("title") or ""):
        notes.append(
            "The title holds a number. A title names what is measured; the number "
            "changes as the data does and the title would not."
        )
    if event is None or event.get("state") not in ("ok", "empty"):
        return notes
    data = event["rows"]["data"]
    kind = widget.get("type")
    if not data:
        notes.append("No rows with these values. Check the window and the filters.")
    elif kind == "kpi" and len(data) > 1:
        notes.append(
            f"A kpi shows one number, but this returns {len(data)} rows. Aggregate it, "
            "or use a bar or a table."
        )
    elif kind == "line" and len(_distinct(event, "x")) < 2:
        notes.append("A line with one point reads better as a kpi.")
    elif kind == "bar" and len(data) > READABLE_BARS:
        notes.append(
            f"{len(data)} bars are hard to compare. Keep the largest {READABLE_BARS} with "
            "a limit, or use a table."
        )
    measures = (event.get("chart") or {}).get("y") or []
    units = {(m.get("format"), m.get("unit"), m.get("currency")) for m in measures}
    if kind in ("line", "bar") and len(units) > 1:
        notes.append(
            "Its measures have different formats or units but would share one axis. "
            "Give each its own widget."
        )
    return notes


def _distinct(event: dict, axis: str) -> set:
    """The distinct values on one axis of a drawn widget."""
    field = ((event.get("chart") or {}).get(axis) or {}).get("field")
    names = [c["name"] for c in event["rows"]["columns"]]
    if field not in names:
        return set()
    at = names.index(field)
    return {row[at] for row in event["rows"]["data"]}


def chat_chart(widget: dict, result: dict) -> tuple[dict, dict]:
    """The widget a chart in the chat draws, and its view event, from a query's rows.

    ``result`` is what :meth:`Gateway.own_result` returned. The widget takes the
    query's SQL, so it is a complete dashboard widget. Raises :class:`ChartRefused`
    for a widget that is not one, or a result a chart should not draw.
    """
    if "query" in widget or "sql" in widget:
        raise ChartRefused(
            "Give the widget its columns, not a query or SQL: it draws the query you "
            "name, and takes that query's SQL."
        )
    if widget.get("type") not in CHART_TYPES:
        raise ChartRefused(f"A chart is one of: {', '.join(CHART_TYPES)}.")
    total, kept = result["total"], len(result["rows"])
    produced = max(total, result.get("row_count") or 0)
    if produced > CHART_MAX_ROWS or kept < total:
        raise ChartRefused(
            f"That result has {produced} rows, more than a chart draws ({CHART_MAX_ROWS}). "
            "Aggregate or limit it in a new query, and draw that."
        )
    if widget["type"] == "bar" and total > BAR_LIMIT:
        raise ChartRefused(
            f"A bar chart draws at most {BAR_LIMIT} bars and that result has {total} rows. "
            "Keep the largest with ORDER BY and LIMIT in a new query, or use a table."
        )
    drawn_widget = {**widget, "sql": result["sql"]}
    try:
        spec = DashboardSpec.model_validate(
            {"version": 1, "name": "chat", "title": "Chat", "rows": [{"widgets": [drawn_widget]}]}
        )
    except ValidationError as exc:
        raise ChartRefused(_first_problem(exc)) from None
    parsed = spec.rows[0].widgets[0]
    chart = chart_spec(parsed)
    bound = [m.field for m in chart.y]
    bound += [axis.field for axis in (chart.x, chart.series) if axis is not None]
    missing = [name for name in bound if name not in result["columns"]]
    if missing:
        raise ChartRefused(
            f"The result has no column {missing[0]!r}; it has {', '.join(result['columns'])}."
        )
    page = RowsPageOut(
        rows=result["rows"],
        columns=result["columns"],
        cursor=None,
        total=total,
        column_schema=result.get("column_schema"),
    )
    columns, data, chart, left_out = drawn(parsed, chart, page)
    event = {
        "type": "widget",
        "id": parsed.id,
        "state": "ok" if data else "empty",
        "scope": {"window": None, "compare_window": None, "applied": [], "skipped": []},
        "rows": {
            "columns": columns,
            "data": data,
            "truncated": False,
            "query_id": result["query_id"],
        },
        "chart": chart.model_dump(mode="json"),
        "sql": result["sql"],
        "warnings": left_out,
    }
    return drawn_widget, event


def result_hash(event: dict) -> str:
    """A digest of what a chart drew, so a later look can tell whether the data moved."""
    rows = event["rows"]
    drawn_rows = [[c["name"] for c in rows["columns"]], rows["data"]]
    return hashlib.sha256(json.dumps(drawn_rows, default=str).encode()).hexdigest()


def _first_problem(exc: ValidationError) -> str:
    error = exc.errors()[0]
    # The path inside the one-widget file, from the widget's own fields down.
    where = ".".join(str(part) for part in error["loc"][4:] if not str(part).endswith("Widget"))
    message = error["msg"].removeprefix("Value error, ")
    return f"{where}: {message}" if where else message
