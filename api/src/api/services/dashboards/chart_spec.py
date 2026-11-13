"""What a widget draws, described without naming a chart library.

The web app draws with Recharts, the assistant describes a chart in words, and a
later renderer could draw images; all of them read this spec. The encoding is
decided once, here, from the widget's type and the shape of its query. A
dashboard file has no styling options to override it.

How numbers look comes from the metric in the semantic layer (format, unit,
currency, polarity), so revenue reads the same on every dashboard. Series keys
and their colour slots depend on the rows a run returns, so they are filled in
when the widget runs.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal

from pydantic import BaseModel, Field

from api.services.dashboards.spec import (
    BAR_LIMIT,
    TABLE_LIMIT,
    BarWidget,
    DataWidget,
    KpiWidget,
    LineWidget,
)
from api.services.semantic.model import Metric

#: Categorical colour slots. A ninth series has no colour of its own.
MAX_SERIES = 8
#: Only quantities that add up can be stacked into a meaningful total.
STACKABLE = frozenset({"sum", "count"})


class Axis(BaseModel):
    field: str
    kind: Literal["time", "category"]
    grain: str | None = None


class Measure(BaseModel):
    field: str
    label: str
    format: str | None = None
    unit: str | None = None
    currency: str | None = None
    polarity: str | None = None


class Series(BaseModel):
    field: str
    keys: list[str] = Field(default_factory=list, description="Filled when the widget runs.")


class Limits(BaseModel):
    rows: int | None = Field(description="Rows the widget reads; None leaves it to the compiler.")
    series: int = MAX_SERIES


class ChartSpec(BaseModel):
    type: Literal["kpi", "line", "bar", "table"]
    x: Axis | None = None
    y: list[Measure]
    series: Series | None = None
    stack: bool = False
    orientation: Literal["vertical", "horizontal"] | None = None
    colors: dict[str, int] = Field(
        default_factory=dict,
        description="Series key to colour slot, filled when the widget runs.",
    )
    limits: Limits


def chart_spec(widget: DataWidget, metrics: Mapping[str, Metric] | None = None) -> ChartSpec:
    """The chart for ``widget``. ``metrics`` are its query's metric definitions."""
    if widget.query is not None:
        return _from_query(widget, metrics or {})
    return _from_columns(widget)


def _field(dimension: str) -> str:
    # The compiler names a dimension's column by its bare name.
    return dimension.rsplit(".", 1)[-1]


def _measure(name: str, metric: Metric | None) -> Measure:
    if metric is None:
        return Measure(field=name, label=name)
    return Measure(
        field=name,
        label=metric.label,
        format=metric.format,
        unit=metric.unit,
        currency=metric.currency,
        polarity=metric.polarity,
    )


def _from_query(widget: DataWidget, metrics: Mapping[str, Metric]) -> ChartSpec:
    query = widget.query
    y = [_measure(name, metrics.get(name)) for name in query.metrics]

    if isinstance(widget, KpiWidget):
        return ChartSpec(type="kpi", y=y, limits=Limits(rows=1))

    if isinstance(widget, LineWidget):
        return ChartSpec(
            type="line",
            x=Axis(field=query.grain, kind="time", grain=query.grain),
            y=y,
            series=Series(field=_field(query.dimensions[0])) if query.dimensions else None,
            limits=Limits(rows=query.limit),
        )

    if isinstance(widget, BarWidget):
        category = _field(query.dimensions[0])
        defined = [metrics.get(name) for name in query.metrics]
        # Stacked only when the bars are parts of one total: additive, and in
        # one format and unit, so the stack's height means something.
        stack = (
            len(defined) > 1
            and all(m is not None and m.agg in STACKABLE for m in defined)
            and len({(m.format, m.unit, m.currency) for m in defined}) == 1
        )
        # Ranked bars read best horizontal, largest first. A bar ordered by its own
        # category keeps that order, left to right.
        first = query.order_by[0].field if query.order_by else None
        natural = first in (category, query.dimensions[0])
        return ChartSpec(
            type="bar",
            x=Axis(field=category, kind="category"),
            y=y,
            stack=stack,
            orientation="vertical" if natural else "horizontal",
            limits=Limits(rows=query.limit or BAR_LIMIT),
        )

    return ChartSpec(type="table", y=y, limits=Limits(rows=query.limit or TABLE_LIMIT))


def _from_columns(widget: DataWidget) -> ChartSpec:
    columns = getattr(widget, "columns", None)
    if isinstance(widget, KpiWidget):
        return ChartSpec(type="kpi", y=[_measure(columns.value, None)], limits=Limits(rows=1))
    if isinstance(widget, LineWidget):
        return ChartSpec(
            type="line",
            x=Axis(field=columns.x, kind="time"),
            y=[_measure(name, None) for name in columns.y],
            series=Series(field=columns.series) if columns.series else None,
            limits=Limits(rows=None),
        )
    if isinstance(widget, BarWidget):
        return ChartSpec(
            type="bar",
            x=Axis(field=columns.x, kind="category"),
            y=[_measure(name, None) for name in columns.y],
            orientation="horizontal",
            limits=Limits(rows=BAR_LIMIT),
        )
    return ChartSpec(type="table", y=[], limits=Limits(rows=TABLE_LIMIT))
