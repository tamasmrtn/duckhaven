"""Turn a dashboard and the viewer's parameter values into what each widget runs.

Pure: no database and no clock. The caller passes ``today`` in the workspace's
time zone, and ``reachable(model, metric)``, the dimensions the semantic layer
says that metric can be sliced by. The same inputs always give the same queries,
which is what lets a run be cached, replayed and explained.

The rules, per widget:

- A **time_range parameter** applies where the widget's query says
  ``time_range: $name``, and nowhere else. A six-month trend can sit beside a
  month-to-date number.
- A **dimension parameter** filters every query widget over the same model, with
  no YAML, unless the widget lists it under ``ignore``, plots that dimension
  itself (a bar chart of regions keeps every bar when a region is picked), or
  holds a metric that cannot reach the dimension.
- An **SQL widget** takes exactly the parameters its ``params`` binds.

Each widget records which parameters it applied and which it did not, and why,
so the page can say "not filtered by region" rather than leave the viewer to
assume it was.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass
from datetime import date, timedelta

from pydantic import ValidationError

from api.schemas.query import TypedParameter
from api.schemas.semantic import TimeRangeIn
from api.services.dashboards.spec import (
    BAR_LIMIT,
    TABLE_LIMIT,
    TIME_PRESETS,
    BarWidget,
    DashboardSpec,
    DashboardTimeRange,
    DataWidget,
    DimensionParameter,
    KpiWidget,
    TableWidget,
    TextWidget,
    TimeRangeParameter,
)
from api.services.semantic.compile import DimensionFilter, MetricQuery, OrderTerm
from api.services.semantic.errors import SemanticError
from api.services.semantic.timespec import TimeRange, compare_window
from api.services.semantic.timespec import resolve as resolve_window

#: Why a set dimension parameter did not filter a widget.
IGNORED = "ignored"  # the widget lists it under ignore
OWN_DIMENSION = "own_dimension"  # the widget plots that dimension itself
OTHER_MODEL = "other_model"  # the widget reads a different model
UNREACHABLE = "unreachable"  # a metric of the widget cannot reach the dimension
NOT_BOUND = "not_bound"  # an SQL widget whose params does not bind it

Reachable = Callable[[str, str], Collection[str]]

_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


class ParameterError(ValueError):
    """A parameter value the viewer sent does not fit its parameter."""

    def __init__(self, name: str, message: str):
        self.name = name
        super().__init__(message)


@dataclass(frozen=True)
class Window:
    """A resolved window, half-open: ``end`` is the first day not included."""

    start: date
    end: date
    label: str

    @property
    def text(self) -> str:
        """How the page names it: "Last 30 days: 14 Oct – 12 Nov 2026"."""
        span = _span(self.start, self.end)
        return f"{self.label}: {span}" if self.label else span


@dataclass(frozen=True)
class ResolvedParameter:
    name: str
    type: str
    #: What the viewer chose, or the default: a preset name, a window, a value, or None.
    value: object
    window: Window | None = None
    #: The window as requested, before resolution, for a time_range parameter.
    requested: TimeRangeIn | None = None


@dataclass(frozen=True)
class Skipped:
    parameter: str
    reason: str


@dataclass(frozen=True)
class ResolvedWidget:
    id: str
    #: Set for a query widget: the request to compile against ``model``.
    query: MetricQuery | None = None
    model: str | None = None
    #: Set for an SQL widget: the statement and its placeholder values.
    sql: str | None = None
    sql_params: Mapping[str, object] | None = None
    window: Window | None = None
    compare: Window | None = None
    applied: tuple[str, ...] = ()
    skipped: tuple[Skipped, ...] = ()
    #: The widget cannot run with these values; the rest of the dashboard can.
    error: str | None = None


@dataclass(frozen=True)
class Resolved:
    today: date
    parameters: dict[str, ResolvedParameter]
    #: Every widget that runs a query, by id. Text widgets run nothing.
    widgets: dict[str, ResolvedWidget]


def resolve(
    spec: DashboardSpec,
    values: Mapping[str, object],
    *,
    today: date,
    reachable: Reachable,
    compare: str | None = None,
) -> Resolved:
    """Resolve every data widget of ``spec`` for one view.

    ``compare``, when set, is the comparison every number (kpi) widget makes in
    place of the one its file sets."""
    unknown = sorted(set(values) - set(spec.parameters))
    if unknown:
        raise ParameterError(
            unknown[0],
            f"This dashboard has no parameter {unknown[0]!r}; "
            f"it has {', '.join(sorted(spec.parameters)) or 'none'}.",
        )
    parameters = {
        name: _parameter(name, param, values.get(name, _DEFAULT), today)
        for name, param in spec.parameters.items()
    }
    widgets = {}
    for _, widget in spec.widgets():
        if isinstance(widget, TextWidget):
            continue
        if widget.query is not None:
            resolved = _query_widget(spec, widget, parameters, today, reachable, compare)
        else:
            resolved = _sql_widget(spec, widget, parameters)
        widgets[widget.id] = resolved
    return Resolved(today=today, parameters=parameters, widgets=widgets)


_DEFAULT = object()


def _parameter(name: str, param, value: object, today: date) -> ResolvedParameter:
    if isinstance(param, DimensionParameter):
        if value is _DEFAULT:
            value = param.default
        if isinstance(value, list | dict):
            raise ParameterError(name, f"{name!r} holds one value of {param.dimension}.")
        return ResolvedParameter(name=name, type=param.type, value=value)

    if value is _DEFAULT:
        value = param.default
    if value is None:
        return ResolvedParameter(name=name, type=param.type, value=None)
    if isinstance(value, str):
        if value not in TIME_PRESETS:
            raise ParameterError(
                name, f"{value!r} is not a window; use one of {', '.join(TIME_PRESETS)}."
            )
        window_in = TIME_PRESETS[value]
    elif isinstance(value, TimeRangeIn):
        window_in = value
    else:
        try:
            window_in = DashboardTimeRange.model_validate(value)
        except ValidationError as exc:
            reason = exc.errors()[0]["msg"].removeprefix("Value error, ")
            raise ParameterError(name, f"{name!r} is not a valid window: {reason}") from exc
    return ResolvedParameter(
        name=name,
        type=param.type,
        value=value,
        window=_window(window_in, today),
        requested=window_in,
    )


def _window(window_in: TimeRangeIn, today: date) -> Window:
    start, end = resolve_window(_time_range(window_in), today=today)
    return Window(start=start, end=end, label=_label(window_in))


def _time_range(window_in: TimeRangeIn) -> TimeRange:
    return TimeRange(
        kind=window_in.kind,
        grain=window_in.grain,
        n=window_in.n,
        start=window_in.start,
        end=window_in.end,
    )


def _query_widget(
    spec: DashboardSpec,
    widget: DataWidget,
    parameters: dict[str, ResolvedParameter],
    today: date,
    reachable: Reachable,
    compare_to: str | None = None,
) -> ResolvedWidget:
    query = widget.query
    kind = compare_to if compare_to is not None and isinstance(widget, KpiWidget) else query.compare
    applied: list[str] = []
    skipped: list[Skipped] = []

    window: Window | None = None
    window_in: TimeRangeIn | None = None
    if isinstance(query.time_range, str):
        name = query.time_range[1:]
        param = parameters[name]
        if param.window is not None:
            window, window_in = param.window, param.requested
            applied.append(name)
    elif query.time_range is not None:
        window_in = query.time_range
        window = _window(window_in, today)

    filters = [
        DimensionFilter(dimension=f.dimension, op=f.op, values=tuple(f.values))
        for f in query.filters
    ]
    for name, spec_param in spec.parameters.items():
        if not isinstance(spec_param, DimensionParameter) or parameters[name].value is None:
            continue
        reason = _why_not(widget, spec_param, name, reachable)
        if reason:
            skipped.append(Skipped(parameter=name, reason=reason))
            continue
        filters.append(
            DimensionFilter(
                dimension=spec_param.dimension, op="eq", values=(parameters[name].value,)
            )
        )
        applied.append(name)

    compare: Window | None = None
    error: str | None = None
    if kind is not None and window_in is not None:
        try:
            start, end = compare_window(_time_range(window_in), kind, today=today)
        except SemanticError as exc:
            error = str(exc)
        else:
            label = "Previous period" if kind == "previous_period" else "Same dates a year earlier"
            compare = Window(start=start, end=end, label=label)

    order_by = tuple(OrderTerm(field=o.field, descending=o.descending) for o in query.order_by)
    limit = query.limit
    if isinstance(widget, BarWidget):
        limit = limit or BAR_LIMIT
        # Ranked bars read best largest first.
        order_by = order_by or (OrderTerm(field=query.metrics[0], descending=True),)
    elif isinstance(widget, TableWidget):
        limit = limit or TABLE_LIMIT

    compiled = MetricQuery(
        metrics=tuple(query.metrics),
        dimensions=tuple(query.dimensions),
        grain=query.grain,
        time_range=None if window_in is None else _time_range(window_in),
        filters=tuple(filters),
        order_by=order_by,
        limit=limit,
        compare=kind if compare is not None else None,
    )
    return ResolvedWidget(
        id=widget.id,
        query=None if error else compiled,
        model=query.model,
        window=window,
        compare=compare,
        applied=tuple(applied),
        skipped=tuple(skipped),
        error=error,
    )


def _why_not(
    widget: DataWidget, param: DimensionParameter, name: str, reachable: Reachable
) -> str | None:
    query = widget.query
    if name in widget.ignore:
        return IGNORED
    if query.model != param.model:
        return OTHER_MODEL
    if param.dimension in query.dimensions:
        return OWN_DIMENSION
    if any(param.dimension not in reachable(query.model, metric) for metric in query.metrics):
        return UNREACHABLE
    return None


def _sql_widget(
    spec: DashboardSpec, widget: DataWidget, parameters: dict[str, ResolvedParameter]
) -> ResolvedWidget:
    bound: dict[str, object] = {}
    applied: list[str] = []
    for placeholder, binding in widget.params.items():
        name, _, part = binding[1:].partition(".")
        param = parameters[name]
        if param.type == "time_range":
            day = None if param.window is None else getattr(param.window, part)
            bound[placeholder] = TypedParameter(
                type="DATE", value=None if day is None else day.isoformat()
            )
            if param.window is not None and name not in applied:
                applied.append(name)
        else:
            bound[placeholder] = param.value
            if param.value is not None and name not in applied:
                applied.append(name)

    named = {binding[1:].partition(".")[0] for binding in widget.params.values()}
    skipped = tuple(
        Skipped(parameter=name, reason=NOT_BOUND)
        for name, param in spec.parameters.items()
        if name not in named and parameters[name].value is not None
    )
    windows = [
        parameters[name].window
        for name in named
        if isinstance(spec.parameters[name], TimeRangeParameter) and parameters[name].window
    ]
    return ResolvedWidget(
        id=widget.id,
        sql=widget.sql,
        sql_params=bound,
        window=windows[0] if len(windows) == 1 else None,
        applied=tuple(applied),
        skipped=skipped,
    )


def _label(window_in: TimeRangeIn) -> str:
    kind, grain, n = window_in.kind, window_in.grain, window_in.n
    if kind == "absolute":
        return ""
    if kind == "to_date":
        return "Today" if grain == "day" else f"{grain.capitalize()} to date"
    plural = f"{grain}s" if n != 1 else grain
    if kind == "trailing":
        # Relative windows start on a period boundary, so one trailing week is
        # this week so far, not the last seven days.
        if n == 1:
            return "Today" if grain == "day" else f"This {grain}"
        return f"Last {n} {plural}"
    if n == 1:
        return "Yesterday" if grain == "day" else f"Last {grain}"
    return f"Last {n} complete {plural}"


def _day(d: date, *, year: bool) -> str:
    text = f"{d.day} {_MONTHS[d.month - 1]}"
    return f"{text} {d.year}" if year else text


def _span(start: date, end: date) -> str:
    last = end - timedelta(days=1)
    if last == start:
        return _day(start, year=True)
    if (start.year, start.month) == (last.year, last.month):
        return f"{start.day} – {_day(last, year=True)}"
    return f"{_day(start, year=start.year != last.year)} – {_day(last, year=True)}"
