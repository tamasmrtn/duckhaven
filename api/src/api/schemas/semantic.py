"""Wire shapes for the semantic layer.

Two things here differ from the rest of the API and both are deliberate.

Everything a caller sends for a *query* is structured — dimension names, an
operator from a fixed set, values as JSON, a time window as a kind and a count.
There is no field into which a caller may put SQL. The only SQL that ever enters
the system is the ``expr`` and ``filter`` an author writes on a definition, and
those are parsed rather than interpolated.

And the read models carry ``validation_state`` alongside the definition rather
than filtering broken things out silently. A metric whose column was dropped is
something a person needs to see and fix; hiding it from its own author would just
make it look like it was never saved.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from api.services.semantic.compile import FILTER_OPS
from api.services.semantic.model import AGGREGATIONS, TIME_GRAINS
from api.services.semantic.timespec import GRAINS, WINDOW_KINDS

ModelStatus = Literal["draft", "published", "deprecated"]
MetricStatus = Literal["draft", "published", "deprecated"]
ValidationState = Literal["ok", "broken", "unchecked"]
DimensionKind = Literal["categorical", "time"]
Cardinality = Literal["many_to_one", "one_to_one"]
Aggregation = Literal["sum", "count", "count_distinct", "avg", "min", "max"]
TimeGrain = Literal["day", "week", "month", "quarter", "year"]
FilterOp = Literal[
    "eq", "ne", "in", "not_in", "gt", "gte", "lt", "lte", "contains", "is_null", "is_not_null"
]
WindowKind = Literal["last_complete", "trailing", "to_date", "absolute"]

# Names become SQL aliases and table aliases, so they have to be identifiers.
# Enforced at the edge rather than escaped later: a definition whose name needs
# quoting is a definition somebody will misread.
_NAME_PATTERN = r"^[a-z][a-z0-9_]*$"

# Sample values are a hint for resolving what a user said to what is stored, not
# a copy of the column.
MAX_SAMPLE_VALUES = 20
MAX_SYNONYMS = 20


class JoinColumn(BaseModel):
    left: str = Field(max_length=255)
    right: str = Field(max_length=255)


# ── Definitions in ────────────────────────────────────────────────────────────


class DatasetIn(BaseModel):
    name: str = Field(pattern=_NAME_PATTERN, max_length=255)
    description: str | None = None
    synonyms: list[str] = Field(default_factory=list, max_length=MAX_SYNONYMS)
    catalog: str = Field(max_length=255, description="Catalog slug the table lives in.")
    schema_name: str = Field(max_length=255)
    table_name: str = Field(max_length=255)
    primary_key: list[str] = Field(default_factory=list)


class DatasetUpdate(BaseModel):
    description: str | None = None
    synonyms: list[str] | None = Field(default=None, max_length=MAX_SYNONYMS)
    catalog: str | None = None
    schema_name: str | None = None
    table_name: str | None = None
    primary_key: list[str] | None = None


class DimensionIn(BaseModel):
    name: str = Field(pattern=_NAME_PATTERN, max_length=255)
    dataset: str = Field(description="Logical dataset name within this model.")
    display_name: str | None = Field(default=None, max_length=255)
    description: str | None = None
    synonyms: list[str] = Field(default_factory=list, max_length=MAX_SYNONYMS)
    kind: DimensionKind = "categorical"
    expr: str | None = Field(
        default=None, description="Scalar SQL expression. Defaults to the dimension name."
    )
    data_type: str | None = Field(default=None, max_length=64)
    time_grains: list[TimeGrain] = Field(default_factory=list)
    is_default_time: bool = False
    sample_values: list[str] = Field(default_factory=list, max_length=MAX_SAMPLE_VALUES)

    @model_validator(mode="after")
    def _time_defaults(self) -> DimensionIn:
        if self.kind == "time" and not self.time_grains:
            # Offering every grain is the least surprising default; an author who
            # wants to restrict a monthly column to monthly says so explicitly.
            object.__setattr__(self, "time_grains", list(TIME_GRAINS))
        if self.kind != "time" and self.is_default_time:
            raise ValueError("Only a time dimension can be the default time axis.")
        return self


class DimensionUpdate(BaseModel):
    display_name: str | None = None
    description: str | None = None
    synonyms: list[str] | None = Field(default=None, max_length=MAX_SYNONYMS)
    kind: DimensionKind | None = None
    expr: str | None = None
    data_type: str | None = None
    time_grains: list[TimeGrain] | None = None
    is_default_time: bool | None = None
    sample_values: list[str] | None = Field(default=None, max_length=MAX_SAMPLE_VALUES)


class MetricIn(BaseModel):
    name: str = Field(pattern=_NAME_PATTERN, max_length=255)
    dataset: str
    display_name: str | None = Field(default=None, max_length=255)
    description: str | None = None
    synonyms: list[str] = Field(default_factory=list, max_length=MAX_SYNONYMS)
    agg: Aggregation
    expr: str | None = None
    filter: str | None = None
    time_dimension: str | None = Field(
        default=None,
        description=(
            "The time dimension this metric is measured on. Strongly recommended: "
            "without it, a time-filtered answer may silently use the wrong date column."
        ),
    )
    caveat: str | None = None

    @model_validator(mode="after")
    def _needs_an_expression(self) -> MetricIn:
        if self.agg != "count" and not self.expr:
            raise ValueError(
                f"{self.agg} needs an expression to aggregate; only count may omit one."
            )
        return self

    @field_validator("agg")
    @classmethod
    def _known_agg(cls, value: str) -> str:
        if value not in AGGREGATIONS:
            raise ValueError(f"Unsupported aggregation {value!r}.")
        return value


class MetricUpdate(BaseModel):
    display_name: str | None = None
    description: str | None = None
    synonyms: list[str] | None = Field(default=None, max_length=MAX_SYNONYMS)
    agg: Aggregation | None = None
    expr: str | None = None
    filter: str | None = None
    time_dimension: str | None = None
    caveat: str | None = None
    status: MetricStatus | None = None


class RelationshipIn(BaseModel):
    name: str = Field(pattern=_NAME_PATTERN, max_length=255)
    left_dataset: str = Field(description="The many side. Traversal starts here.")
    right_dataset: str = Field(description="The unique side. Must declare a primary key.")
    join_columns: list[JoinColumn] = Field(min_length=1)
    cardinality: Cardinality = "many_to_one"


class ModelIn(BaseModel):
    slug: str = Field(pattern=_NAME_PATTERN, max_length=255)
    name: str = Field(max_length=255)
    description: str | None = None


class ModelUpdate(BaseModel):
    name: str | None = None
    description: str | None = None


# ── Definitions out ───────────────────────────────────────────────────────────


class DatasetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    description: str | None
    synonyms: list[str] = Field(default_factory=list)
    catalog: str | None = None
    schema_name: str
    table_name: str
    primary_key: list[str] = Field(default_factory=list)
    validation_state: ValidationState
    validation_detail: str | None = None


class DimensionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    dataset: str | None = None
    display_name: str | None
    description: str | None
    synonyms: list[str] = Field(default_factory=list)
    kind: DimensionKind
    expr: str
    data_type: str | None
    time_grains: list[str] = Field(default_factory=list)
    is_default_time: bool
    sample_values: list[str] = Field(default_factory=list)
    validation_state: ValidationState
    validation_detail: str | None = None


class MetricOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    dataset: str | None = None
    display_name: str | None
    description: str | None
    synonyms: list[str] = Field(default_factory=list)
    agg: Aggregation
    expr: str | None
    filter: str | None
    time_dimension: str | None = None
    caveat: str | None
    status: MetricStatus
    # A readable rendering of the calculation, so "how is this computed?" has an
    # answer that does not require reading two other fields and knowing SQL.
    expression: str | None = None
    validation_state: ValidationState
    validation_detail: str | None = None


class RelationshipOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    left_dataset: str | None = None
    right_dataset: str | None = None
    join_columns: list[JoinColumn] = Field(default_factory=list)
    cardinality: Cardinality
    validation_state: ValidationState
    validation_detail: str | None = None


class ModelSummaryOut(BaseModel):
    """The list view: enough to choose a model without loading it."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    slug: str
    name: str
    description: str | None
    status: ModelStatus
    provider: str
    owner_id: uuid.UUID | None
    metric_count: int = 0
    dimension_count: int = 0
    dataset_count: int = 0
    broken_count: int = 0
    created_at: datetime
    updated_at: datetime


class ModelOut(ModelSummaryOut):
    datasets: list[DatasetOut] = Field(default_factory=list)
    dimensions: list[DimensionOut] = Field(default_factory=list)
    metrics: list[MetricOut] = Field(default_factory=list)
    relationships: list[RelationshipOut] = Field(default_factory=list)


class ValidationErrorOut(BaseModel):
    kind: str
    name: str
    detail: str


class ValidationReportOut(BaseModel):
    ok: bool
    errors: list[ValidationErrorOut] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    checked_at: datetime | None = None


# ── Search ────────────────────────────────────────────────────────────────────


class SemanticHitOut(BaseModel):
    kind: Literal["metric", "dimension"]
    model: str
    name: str
    label: str
    description: str | None = None
    synonyms: list[str] = Field(default_factory=list)
    status: str
    expression: str | None = None
    time_dimension: str | None = None
    caveat: str | None = None
    dimension_kind: str | None = None
    sample_values: list[str] = Field(default_factory=list)


class BrokenDefinitionOut(BaseModel):
    """A definition the question matches that cannot currently be used."""

    kind: Literal["metric", "dimension"]
    model: str
    name: str
    detail: str


class SemanticSearchOut(BaseModel):
    hits: list[SemanticHitOut] = Field(default_factory=list)
    # Metrics tied at the top of the ranking. Populated when a term matches more
    # than one authoritative definition equally well — "how many customers?"
    # against both `total_customers` and `active_customers`. The caller is meant
    # to ask rather than pick.
    ambiguous: list[SemanticHitOut] = Field(default_factory=list)
    # Definitions that exist but whose bindings no longer resolve. Kept out of
    # `hits` so nothing queries them, and reported so the caller can say "that is
    # defined but broken" instead of "there is no such thing" — which would send
    # somebody off to rebuild a definition the organization already has.
    broken: list[BrokenDefinitionOut] = Field(default_factory=list)


# ── Query ─────────────────────────────────────────────────────────────────────


class TimeRangeIn(BaseModel):
    """A time window, stated explicitly.

    There is no default, because "last month" means the previous calendar month
    to one person, the trailing thirty days to another, and month-to-date to a
    third — three different numbers from the same words.
    """

    kind: WindowKind
    grain: TimeGrain | None = None
    n: int | None = Field(default=None, ge=1, le=1000)
    start: date | None = None
    end: date | None = None

    @model_validator(mode="after")
    def _shape(self) -> TimeRangeIn:
        if self.kind == "absolute":
            if self.start is None or self.end is None:
                raise ValueError("An absolute window needs both start and end.")
        else:
            if self.grain is None:
                raise ValueError(f"A {self.kind!r} window needs a grain: {', '.join(GRAINS)}.")
            if self.kind != "to_date" and self.n is None:
                raise ValueError(f"A {self.kind!r} window needs a number of periods.")
        return self


class DimensionFilterIn(BaseModel):
    """A predicate on a dimension. ``values`` are data and stay data."""

    dimension: str
    op: FilterOp
    values: list[str | int | float | bool | None] = Field(default_factory=list)

    @field_validator("op")
    @classmethod
    def _known_op(cls, value: str) -> str:
        if value not in FILTER_OPS:
            raise ValueError(f"Unsupported filter operator {value!r}.")
        return value


class OrderTermIn(BaseModel):
    field: str
    descending: bool = False


class MetricQueryIn(BaseModel):
    """A question, in the semantic layer's vocabulary rather than in SQL."""

    model: str
    metrics: list[str] = Field(min_length=1, max_length=10)
    dimensions: list[str] = Field(default_factory=list, max_length=10)
    grain: TimeGrain | None = None
    time_range: TimeRangeIn | None = None
    filters: list[DimensionFilterIn] = Field(default_factory=list, max_length=20)
    order_by: list[OrderTermIn] = Field(default_factory=list, max_length=5)
    limit: int | None = Field(default=None, ge=1, le=5000)


class CompiledQueryOut(BaseModel):
    sql: str
    definitions_used: list[dict] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


# ── Impact ────────────────────────────────────────────────────────────────────


class DependentOut(BaseModel):
    kind: Literal["metric", "dimension"]
    model: str
    model_name: str
    model_status: ModelStatus
    name: str
    label: str
    status: str
    dataset: str
    columns: list[str] = Field(default_factory=list)


class TableSemanticsOut(BaseModel):
    """Which semantic definitions depend on one physical table."""

    dependents: list[DependentOut] = Field(default_factory=list)


class SemanticSkippedOut(BaseModel):
    """One definition the import could not use, and why.

    Reported rather than dropped: a metric that silently failed to import looks
    exactly like one nobody ever wrote, and those need very different responses.
    """

    ref: str
    reason: str
    detail: str | None = None


class SemanticImportOut(BaseModel):
    provider: str
    run_id: str
    created: int = 0
    updated: int = 0
    removed: int = 0
    skipped: list[SemanticSkippedOut] = Field(default_factory=list)


# Re-exported so the router and the assistant tool docstrings can describe the
# window vocabulary without restating it.
__all__ = [name for name in dir() if not name.startswith("_")] + ["WINDOW_KINDS"]
