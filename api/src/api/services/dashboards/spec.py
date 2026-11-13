"""The dashboard file format.

A dashboard is one YAML document: a version, a name, a few parameters, and rows
of widgets. These models are the format's only definition. The loader validates
against them, ``GET /dashboard-schema`` publishes the JSON Schema generated from
them, and every author (a person, the CLI, the assistant, an MCP client) is held
to the same rules.

Two kinds of rule live here:

- **Shape**, on the models: unknown keys are refused rather than ignored, types
  are strict (``yes`` is not a boolean, ``"5"`` is not a number), and each widget
  type accepts only the query shapes it can draw.
- **References**, in :func:`check`: a ``$name`` names a declared parameter of the
  right kind, widget ids are unique, the dashboard stays within its size limits.
  These need the whole document, and they report the exact path they fault.

Whether a model, metric or dimension exists is a question for the semantic layer,
answered when the dashboard is compiled; nothing here touches the database.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Discriminator,
    Field,
    StringConstraints,
    Tag,
    field_validator,
    model_validator,
)

from api.schemas.semantic import (
    DimensionFilterIn,
    MetricQueryIn,
    OrderTermIn,
    TimeRangeIn,
)

#: The version this server writes and documents.
SPEC_VERSION = 1
#: The versions this server reads. The policy is the current version and the one
#: before it: a file one version behind is upgraded in memory as it is read, and
#: the stored source text is never rewritten. Anything else is refused with
#: ``unsupported_spec_version``, so a file written for a newer server never half
#: loads on an older one.
SUPPORTED_SPEC_VERSIONS = (1,)

GRID_COLUMNS = 12
MAX_ROWS = 20
MAX_WIDGETS_PER_ROW = 8
MAX_WIDGETS = 40
#: Every widget runs when the dashboard is opened; past this many, opening it
#: gets slow enough to say so.
WIDGET_WARNING = 16
MAX_PARAMETERS = 8
MAX_LABELS = 20
MAX_SQL_LENGTH = 20_000
MAX_MARKDOWN_LENGTH = 4_000
BAR_LIMIT = 50
TABLE_LIMIT = 1_000

#: Named time windows a parameter can default to, in the semantic layer's terms.
TIME_PRESETS: dict[str, TimeRangeIn] = {
    "today": TimeRangeIn(kind="to_date", grain="day"),
    "last_7_days": TimeRangeIn(kind="trailing", grain="day", n=7),
    "last_30_days": TimeRangeIn(kind="trailing", grain="day", n=30),
    "last_90_days": TimeRangeIn(kind="trailing", grain="day", n=90),
    "last_12_months": TimeRangeIn(kind="last_complete", grain="month", n=12),
    "week_to_date": TimeRangeIn(kind="to_date", grain="week"),
    "month_to_date": TimeRangeIn(kind="to_date", grain="month"),
    "quarter_to_date": TimeRangeIn(kind="to_date", grain="quarter"),
    "year_to_date": TimeRangeIn(kind="to_date", grain="year"),
    "last_week": TimeRangeIn(kind="last_complete", grain="week", n=1),
    "last_month": TimeRangeIn(kind="last_complete", grain="month", n=1),
    "last_quarter": TimeRangeIn(kind="last_complete", grain="quarter", n=1),
    "last_year": TimeRangeIn(kind="last_complete", grain="year", n=1),
}

TimePreset = Literal[
    "today",
    "last_7_days",
    "last_30_days",
    "last_90_days",
    "last_12_months",
    "week_to_date",
    "month_to_date",
    "quarter_to_date",
    "year_to_date",
    "last_week",
    "last_month",
    "last_quarter",
    "last_year",
]

NAME_PATTERN = r"^[a-z][a-z0-9_-]*$"
IDENTIFIER_PATTERN = r"^[a-z][a-z0-9_]*$"
PARAM_REF_PATTERN = r"^\$[a-z][a-z0-9_]*$"
BINDING_PATTERN = r"^\$[a-z][a-z0-9_]*(\.(start|end))?$"

#: The dashboard's name: its address in the workspace and its file name.
Name = Annotated[str, StringConstraints(pattern=NAME_PATTERN, max_length=64)]
#: A widget id or parameter name. Both appear in URLs and in ``$name`` references.
Identifier = Annotated[str, StringConstraints(pattern=IDENTIFIER_PATTERN, max_length=64)]
#: ``$name``: a dashboard parameter, used where a query takes a time range.
ParamRef = Annotated[str, StringConstraints(pattern=PARAM_REF_PATTERN, max_length=65)]
#: ``$name``, ``$name.start`` or ``$name.end``: what an SQL placeholder is bound to.
Binding = Annotated[str, StringConstraints(pattern=BINDING_PATTERN, max_length=71)]
Label = Annotated[str, StringConstraints(min_length=1, max_length=40)]
Scalar = str | int | float | bool | None


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


# ── Queries ───────────────────────────────────────────────────────────────────
# The semantic layer's own request models, made strict. A widget's query is the
# same object the compile endpoint takes, so it means the same thing here.


class DashboardTimeRange(TimeRangeIn):
    model_config = ConfigDict(extra="forbid", strict=True)

    # Dates arrive as text in JSON; strict mode would refuse every one of them.
    start: date | None = Field(default=None, strict=False)
    end: date | None = Field(default=None, strict=False)

    @model_validator(mode="after")
    def _ordered(self) -> DashboardTimeRange:
        if self.start is not None and self.end is not None and self.end <= self.start:
            raise ValueError(
                f"The window ends ({self.end}) on or before it starts ({self.start}); "
                "end is exclusive, so it must be later."
            )
        return self


class DashboardFilter(DimensionFilterIn):
    model_config = ConfigDict(extra="forbid", strict=True)


class DashboardOrder(OrderTermIn):
    model_config = ConfigDict(extra="forbid", strict=True)


def _by_value_type(text_tag: str):
    def pick(value: object) -> str:
        return text_tag if isinstance(value, str) else "window"

    return Discriminator(pick)


TimeRangeOrRef = Annotated[
    Annotated[DashboardTimeRange, Tag("window")] | Annotated[ParamRef, Tag("ref")],
    _by_value_type("ref"),
]


class DashboardQuery(MetricQueryIn):
    """A metric query whose time range may be a dashboard parameter."""

    model_config = ConfigDict(extra="forbid", strict=True)

    time_range: TimeRangeOrRef | None = Field(
        default=None,
        description="A window, or $name to use a time_range parameter.",
    )
    filters: list[DashboardFilter] = Field(default_factory=list, max_length=20)
    order_by: list[DashboardOrder] = Field(default_factory=list, max_length=5)


# ── Widgets ───────────────────────────────────────────────────────────────────


class KpiColumns(_Strict):
    value: str = Field(description="The result column holding the number.")


class LineColumns(_Strict):
    x: str = Field(description="The time column.")
    y: list[str] = Field(min_length=1, max_length=10, description="One line per column.")
    series: str | None = Field(
        default=None, description="Split one y column into a line per value of this column."
    )


class BarColumns(_Strict):
    x: str = Field(description="The category column.")
    y: list[str] = Field(min_length=1, max_length=10, description="One bar per column.")


class _Widget(_Strict):
    id: Identifier = Field(description="Unique in the dashboard. Links and filters refer to it.")
    title: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=500)
    width: int | None = Field(
        default=None,
        ge=1,
        le=GRID_COLUMNS,
        description="Columns of the 12-column row. Omitted widths share what is left.",
    )


class _DataWidget(_Widget):
    query: DashboardQuery | None = Field(default=None, description="A metric query.")
    sql: str | None = Field(
        default=None,
        min_length=1,
        max_length=MAX_SQL_LENGTH,
        description="Inline SQL. Use $name placeholders for the values params binds.",
    )
    params: dict[Identifier, Binding] = Field(
        default_factory=dict,
        max_length=20,
        description="SQL placeholders and the parameters they take: $region, $period.start.",
    )
    ignore: list[Identifier] = Field(
        default_factory=list,
        max_length=MAX_PARAMETERS,
        description="Dimension parameters this widget does not apply.",
    )

    @model_validator(mode="after")
    def _check_source(self) -> _DataWidget:
        if (self.query is None) == (self.sql is None):
            raise ValueError("A widget reads from exactly one of query (a metric query) or sql.")
        if self.query is not None:
            if self.params:
                raise ValueError(
                    "params binds SQL placeholders; a query widget takes the dashboard's "
                    "parameters without it."
                )
            if getattr(self, "columns", None) is not None:
                raise ValueError(
                    "columns is for SQL widgets; a query widget's chart follows from its query."
                )
            if self.query.compare is not None:
                if self.type != "kpi":
                    raise ValueError("compare is available on kpi widgets only.")
                if self.query.time_range is None:
                    raise ValueError("compare needs a time_range to compare against.")
            self._check_query(self.query)
        else:
            if self.ignore:
                raise ValueError(
                    "ignore is for query widgets; an SQL widget uses only the parameters "
                    "its params binds."
                )
            self._check_sql()
        return self

    def _check_query(self, query: DashboardQuery) -> None:
        pass

    def _check_sql(self) -> None:
        pass


class KpiWidget(_DataWidget):
    """One number, optionally beside the same number for an earlier window."""

    type: Literal["kpi"]
    columns: KpiColumns | None = None

    def _check_query(self, query: DashboardQuery) -> None:
        if len(query.metrics) != 1:
            raise ValueError(f"A kpi shows one metric; this one asks for {len(query.metrics)}.")
        if query.dimensions:
            raise ValueError(
                "A kpi shows one number, so it takes no dimensions; use a bar or a table "
                "to break it down."
            )
        if query.grain is not None:
            raise ValueError("A kpi takes no grain; use a line to show it over time.")

    def _check_sql(self) -> None:
        if self.columns is None:
            raise ValueError("An SQL kpi names its number: columns: {value: <column>}.")


class LineWidget(_DataWidget):
    """Metrics over time: one line per metric, or per value of one dimension."""

    type: Literal["line"]
    columns: LineColumns | None = None

    def _check_query(self, query: DashboardQuery) -> None:
        if query.grain is None:
            raise ValueError("A line plots over time, so it needs a grain: day, week, month...")
        if len(query.dimensions) > 1:
            raise ValueError("A line splits by at most one dimension.")
        if query.dimensions and len(query.metrics) > 1:
            raise ValueError("A line splits by a dimension only when it plots one metric.")

    def _check_sql(self) -> None:
        if self.columns is None:
            raise ValueError("An SQL line names its columns: columns: {x: <time>, y: [<value>]}.")
        if self.columns.series is not None and len(self.columns.y) > 1:
            raise ValueError("A line splits by series only when it plots one y column.")


class BarWidget(_DataWidget):
    """Metrics compared across the values of one dimension."""

    type: Literal["bar"]
    columns: BarColumns | None = None

    def _check_query(self, query: DashboardQuery) -> None:
        if query.grain is not None:
            raise ValueError("A bar compares categories and takes no grain; use a line for time.")
        if len(query.dimensions) != 1:
            raise ValueError(
                f"A bar has one bar group per value of exactly one dimension; this one "
                f"has {len(query.dimensions)}."
            )
        if query.limit is not None and query.limit > BAR_LIMIT:
            raise ValueError(
                f"A bar shows at most {BAR_LIMIT} bars; set limit to {BAR_LIMIT} or less."
            )

    def _check_sql(self) -> None:
        if self.columns is None:
            raise ValueError(
                "An SQL bar names its columns: columns: {x: <category>, y: [<value>]}."
            )


class TableWidget(_DataWidget):
    """Rows as they come: dimensions first, then metrics."""

    type: Literal["table"]

    def _check_query(self, query: DashboardQuery) -> None:
        if query.limit is not None and query.limit > TABLE_LIMIT:
            raise ValueError(
                f"A table shows at most {TABLE_LIMIT} rows; set limit to {TABLE_LIMIT} or less."
            )


# Links may leave the dashboard only over https, or stay inside DuckHaven.
_CODE = re.compile(r"```.*?```|`[^`\n]*`", re.DOTALL)
_IMAGE = re.compile(r"!\[")
_AUTOLINK = re.compile(r"<([a-zA-Z][a-zA-Z0-9+.-]*:[^>\s]*)>")
_HTML = re.compile(r"</?[a-zA-Z][^>]*>")
_INLINE_LINK = re.compile(r"\]\(\s*<?([^)\s>]*)")
_REFERENCE = re.compile(r"^ {0,3}\[[^\]]+\]:\s*<?([^\s>]+)", re.MULTILINE)
_BARE = re.compile(r"(?<![\w(<])(?:http://|www\.)\S+", re.IGNORECASE)


def _allowed_link(target: str) -> bool:
    if target.lower().startswith("https://"):
        return True
    return target.startswith("/") and not target.startswith("//")


def markdown_problem(text: str) -> str | None:
    """Why a text widget's Markdown is refused, or None.

    The page renders a safe subset regardless. Refusing here tells the author at
    save time instead of leaving them to wonder why an image never appears.
    """
    prose = _CODE.sub("", text)
    if _IMAGE.search(prose):
        return "Text widgets show no images; link to the image instead."
    autolinks = _AUTOLINK.findall(prose)
    if _HTML.search(_AUTOLINK.sub("", prose)):
        return "Text widgets take Markdown, not HTML."
    for target in [*autolinks, *_INLINE_LINK.findall(prose), *_REFERENCE.findall(prose)]:
        if not _allowed_link(target):
            return (
                f"Link {target!r} is refused: links go to https:// addresses or to a "
                "DuckHaven page such as /my-workspace/catalog."
            )
    bare = _BARE.search(prose)
    if bare:
        return f"Link {bare.group(0)!r} is refused: write it as an https:// address."
    return None


class TextWidget(_Strict):
    """A heading or a short note, in Markdown."""

    id: Identifier
    type: Literal["text"]
    title: str | None = Field(default=None, max_length=120)
    width: int | None = Field(default=None, ge=1, le=GRID_COLUMNS)
    markdown: str = Field(min_length=1, max_length=MAX_MARKDOWN_LENGTH)

    @field_validator("markdown")
    @classmethod
    def _safe(cls, value: str) -> str:
        problem = markdown_problem(value)
        if problem:
            raise ValueError(problem)
        return value


Widget = Annotated[
    KpiWidget | LineWidget | BarWidget | TableWidget | TextWidget,
    Field(discriminator="type"),
]
DataWidget = KpiWidget | LineWidget | BarWidget | TableWidget


class Row(_Strict):
    widgets: list[Widget] = Field(min_length=1, max_length=MAX_WIDGETS_PER_ROW)

    @model_validator(mode="after")
    def _fits(self) -> Row:
        fixed = sum(w.width for w in self.widgets if w.width is not None)
        open_ = sum(1 for w in self.widgets if w.width is None)
        if fixed + open_ > GRID_COLUMNS:
            share = (
                f", and {open_} widget{'s' if open_ > 1 else ''} without a width "
                "need at least one column each"
                if open_
                else ""
            )
            raise ValueError(
                f"This row's widths add up to {fixed} of {GRID_COLUMNS} columns{share}."
            )
        return self


# ── Parameters ────────────────────────────────────────────────────────────────


TimeDefault = Annotated[
    Annotated[DashboardTimeRange, Tag("window")] | Annotated[TimePreset, Tag("preset")],
    _by_value_type("preset"),
]


class TimeRangeParameter(_Strict):
    """A window that widgets opt into by writing ``time_range: $name``."""

    type: Literal["time_range"]
    label: str | None = Field(default=None, max_length=80)
    default: TimeDefault | None = Field(
        default=None, description="A preset such as last_30_days, or a window. Omitted: all time."
    )


class DimensionParameter(_Strict):
    """One value of a dimension, applied to every query widget that can use it."""

    type: Literal["dimension"]
    label: str | None = Field(default=None, max_length=80)
    model: Identifier = Field(description="The semantic model the dimension belongs to.")
    dimension: str = Field(min_length=1, max_length=255)
    default: Scalar = None
    multiple: bool = Field(default=False, description="Reserved; only false is accepted.")

    @field_validator("multiple")
    @classmethod
    def _single(cls, value: bool) -> bool:
        if value:
            raise ValueError(
                "A dimension parameter holds one value; multiple: true is not supported yet."
            )
        return value


Parameter = Annotated[TimeRangeParameter | DimensionParameter, Field(discriminator="type")]


# ── The dashboard ─────────────────────────────────────────────────────────────


class DashboardSpec(_Strict):
    """A dashboard, as written in its file."""

    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        title="DuckHaven dashboard",
        json_schema_extra={"$schema": "https://json-schema.org/draft/2020-12/schema"},
    )

    version: Literal[1] = Field(description="The format version this file is written for.")
    name: Name = Field(description="Unique in the workspace; the file is <name>.dashboard.yml.")
    title: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    labels: list[Label] = Field(default_factory=list, max_length=MAX_LABELS)
    parameters: dict[Identifier, Parameter] = Field(default_factory=dict, max_length=MAX_PARAMETERS)
    rows: list[Row] = Field(min_length=1, max_length=MAX_ROWS)

    def widgets(self) -> list[tuple[str, Widget]]:
        """Every widget with its path, in reading order."""
        return [
            (f"rows[{r}].widgets[{w}]", widget)
            for r, row in enumerate(self.rows)
            for w, widget in enumerate(row.widgets)
        ]


def json_schema() -> dict:
    """The JSON Schema for the current version."""
    return DashboardSpec.model_json_schema()


# ── References ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Problem:
    """A reference fault found by :func:`check`, at a dotted path into the file."""

    path: str
    message: str
    warning: bool = False


def check(spec: DashboardSpec) -> list[Problem]:
    """Faults that need the whole document: references, uniqueness and size."""
    problems: list[Problem] = []
    widgets = spec.widgets()

    if len(widgets) > MAX_WIDGETS:
        problems.append(
            Problem(
                "rows",
                f"A dashboard holds at most {MAX_WIDGETS} widgets; this one has {len(widgets)}.",
            )
        )
    elif len(widgets) > WIDGET_WARNING:
        problems.append(
            Problem(
                "rows",
                f"{len(widgets)} widgets all run when the dashboard opens; above "
                f"{WIDGET_WARNING} it opens slowly. Consider a second dashboard.",
                warning=True,
            )
        )

    seen: dict[str, str] = {}
    for path, widget in widgets:
        if widget.id in seen:
            problems.append(
                Problem(
                    f"{path}.id", f"Widget id {widget.id!r} is already used at {seen[widget.id]}."
                )
            )
        else:
            seen[widget.id] = path

    used: set[str] = set()
    for path, widget in widgets:
        if isinstance(widget, TextWidget):
            continue
        problems += _check_widget(spec, path, widget, used)

    for name, param in spec.parameters.items():
        if name in used:
            continue
        if isinstance(param, TimeRangeParameter):
            message = f"No widget uses ${name}; write time_range: ${name} in a widget's query."
        elif any(
            not isinstance(w, TextWidget) and w.query is not None and w.query.model == param.model
            for _, w in widgets
        ):
            continue
        else:
            message = f"No widget reads model {param.model!r}, so ${name} filters nothing."
        problems.append(Problem(f"parameters.{name}", message, warning=True))
    return problems


def _check_widget(
    spec: DashboardSpec, path: str, widget: DataWidget, used: set[str]
) -> list[Problem]:
    problems: list[Problem] = []
    declared = spec.parameters

    def kind_of(name: str) -> str | None:
        param = declared.get(name)
        return None if param is None else param.type

    if widget.query is not None and isinstance(widget.query.time_range, str):
        name = widget.query.time_range[1:]
        used.add(name)
        if kind_of(name) != "time_range":
            problems.append(
                Problem(f"{path}.query.time_range", _not_a(name, "time_range", declared))
            )

    for i, name in enumerate(widget.ignore):
        if kind_of(name) != "dimension":
            problems.append(Problem(f"{path}.ignore[{i}]", _not_a(name, "dimension", declared)))

    for placeholder, binding in widget.params.items():
        name, _, part = binding[1:].partition(".")
        used.add(name)
        kind = kind_of(name)
        where = f"{path}.params.{placeholder}"
        if kind is None:
            problems.append(Problem(where, _not_a(name, None, declared)))
        elif kind == "time_range" and not part:
            problems.append(
                Problem(where, f"${name} is a window; bind ${name}.start or ${name}.end.")
            )
        elif kind == "dimension" and part:
            problems.append(Problem(where, f"${name} is one value; bind ${name}, without .{part}."))
    return problems


def _not_a(name: str, kind: str | None, declared: dict) -> str:
    param = declared.get(name)
    if param is None:
        names = ", ".join(f"${n}" for n in declared) or "none"
        return f"${name} is not a parameter of this dashboard (declared: {names})."
    return f"${name} is a {param.type} parameter; this needs a {kind} parameter."
