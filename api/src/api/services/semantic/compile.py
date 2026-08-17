"""A metric request in, deterministic SQL out.

This module is the reason the semantic layer is worth building rather than being
a pile of descriptions. The language model chooses *which* metric and *which*
dimensions; it never writes the aggregation, the join or the time filter. So the
class of failure where a plausible-looking ``SUM`` lands on the wrong column, or
a join quietly doubles every total, is not something the model can do well or
badly — it is something it cannot express.

Everything the caller supplies is structured. Metric and dimension names are
looked up in the model; filter values arrive as data and are emitted as
``sqlglot`` literals; time windows arrive as a kind and a count. The only SQL
text in the system is the ``expr`` and ``filter`` an author wrote, and that is
parsed — never interpolated — so a malformed expression fails here rather than
becoming part of a statement.

The output is fully qualified ``catalog.schema.table``, which matters more than it
looks: it means the compiled statement goes through ``POST /queries`` and meets
``sql_guard.assert_allowed`` and ``grants.assert_query_access`` exactly like
hand-written SQL. The semantic layer adds no authorization path of its own, so
there is none to get wrong.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import sqlglot
from sqlglot import exp

from api.services.semantic.errors import SemanticError
from api.services.semantic.joins import merge_paths, reachable, resolve_path
from api.services.semantic.model import LoadedModel, Metric
from api.services.semantic.timespec import TimeRange
from api.services.semantic.timespec import resolve as resolve_window

# Comparison operators a caller may use. Restricted rather than open so a filter
# is always a comparison against bound values — there is no shape of input here
# that becomes SQL text.
FILTER_OPS = (
    "eq",
    "ne",
    "in",
    "not_in",
    "gt",
    "gte",
    "lt",
    "lte",
    "contains",
    "is_null",
    "is_not_null",
)

# A hard ceiling on rows returned, independent of what the caller asks for. The
# result is a sample for a language model to reason over, not an export.
MAX_LIMIT = 5000
DEFAULT_LIMIT = 500

_AGG_NODES = {
    "sum": exp.Sum,
    "avg": exp.Avg,
    "min": exp.Min,
    "max": exp.Max,
}


@dataclass(frozen=True)
class DimensionFilter:
    """A structured predicate on a dimension. Values are data, never SQL."""

    dimension: str
    op: str
    values: tuple = ()


@dataclass(frozen=True)
class OrderTerm:
    field: str
    descending: bool = False


@dataclass(frozen=True)
class MetricQuery:
    """What was asked, in the semantic layer's own vocabulary."""

    metrics: tuple[str, ...]
    dimensions: tuple[str, ...] = ()
    grain: str | None = None
    time_range: TimeRange | None = None
    filters: tuple[DimensionFilter, ...] = ()
    order_by: tuple[OrderTerm, ...] = ()
    limit: int | None = None


@dataclass
class CompiledQuery:
    """The statement, plus everything needed to explain where it came from."""

    sql: str
    definitions_used: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _scalar(sql: str, *, alias: str, what: str) -> exp.Expression:
    """Parse an author-written scalar expression and qualify its bare columns.

    Qualifying is what lets an author write ``total_amount`` and have it stay
    unambiguous once the query joins another table that also has that column —
    the classic way a definition starts silently reading the wrong column the day
    a join is added.
    """
    try:
        statements = sqlglot.parse(sql, read="duckdb")
    except Exception as exc:  # sqlglot.errors.ParseError et al
        raise SemanticError(f"{what} is not valid SQL: {exc}") from exc

    if len(statements) != 1 or statements[0] is None:
        raise SemanticError(f"{what} must be a single expression.")

    tree = statements[0]
    if isinstance(tree, exp.Select | exp.Union | exp.Subquery):
        raise SemanticError(f"{what} must be a scalar expression, not a query.")
    if tree.find(exp.Select) is not None:
        raise SemanticError(f"{what} must not contain a subquery.")

    for column in tree.find_all(exp.Column):
        if not column.table:
            column.set("table", exp.to_identifier(alias))
    return tree


def _aggregate(metric: Metric, alias: str) -> exp.Expression:
    """Build the metric's aggregate call, filter included."""
    if metric.agg == "count":
        inner: exp.Expression = (
            exp.Star()
            if not metric.expr
            else _scalar(metric.expr, alias=alias, what=f"metric {metric.name!r} expression")
        )
        agg: exp.Expression = exp.Count(this=inner)
    elif metric.agg == "count_distinct":
        if not metric.expr:
            raise SemanticError(
                f"Metric {metric.name!r} counts distinct values but names no expression."
            )
        target = _scalar(metric.expr, alias=alias, what=f"metric {metric.name!r} expression")
        agg = exp.Count(this=exp.Distinct(expressions=[target]))
    else:
        node = _AGG_NODES.get(metric.agg)
        if node is None:
            raise SemanticError(
                f"Metric {metric.name!r} uses unsupported aggregation {metric.agg!r}."
            )
        if not metric.expr:
            raise SemanticError(
                f"Metric {metric.name!r} aggregates with {metric.agg} but names no expression."
            )
        agg = node(
            this=_scalar(metric.expr, alias=alias, what=f"metric {metric.name!r} expression")
        )

    if metric.filter:
        condition = _scalar(metric.filter, alias=alias, what=f"metric {metric.name!r} filter")
        agg = exp.Filter(this=agg, expression=exp.Where(this=condition))
    return agg


def _literal(value) -> exp.Expression:
    """A caller-supplied value, as a bound literal.

    Everything arrives as data and leaves as a literal node. There is no path by
    which a value becomes part of the statement's syntax.
    """
    if value is None:
        return exp.null()
    if isinstance(value, bool):
        return exp.true() if value else exp.false()
    if isinstance(value, int | float):
        return exp.Literal.number(value)
    return exp.Literal.string(str(value))


def _predicate(dim_expr: exp.Expression, filt: DimensionFilter) -> exp.Expression:
    op = filt.op
    if op == "is_null":
        return exp.Is(this=dim_expr, expression=exp.null())
    if op == "is_not_null":
        return exp.Not(this=exp.Is(this=dim_expr, expression=exp.null()))

    if op in ("in", "not_in"):
        if not filt.values:
            raise SemanticError(
                f"Filter on {filt.dimension!r} with {op!r} needs at least one value."
            )
        node: exp.Expression = exp.In(this=dim_expr, expressions=[_literal(v) for v in filt.values])
        return exp.Not(this=node) if op == "not_in" else node

    if not filt.values:
        raise SemanticError(f"Filter on {filt.dimension!r} with {op!r} needs a value.")
    value = _literal(filt.values[0])

    if op == "contains":
        # LIKE with the wildcards added here rather than by the caller, so a value
        # containing % or _ cannot silently widen its own match.
        escaped = str(filt.values[0]).replace("%", r"\%").replace("_", r"\_")
        return exp.Like(this=dim_expr, expression=exp.Literal.string(f"%{escaped}%"))

    mapping = {
        "eq": exp.EQ,
        "ne": exp.NEQ,
        "gt": exp.GT,
        "gte": exp.GTE,
        "lt": exp.LT,
        "lte": exp.LTE,
    }
    node_type = mapping.get(op)
    if node_type is None:
        raise SemanticError(f"Unsupported filter operator {op!r}.", alternatives=list(FILTER_OPS))
    return node_type(this=dim_expr, expression=value)


def _table(model: LoadedModel, dataset_name: str) -> exp.Expression:
    ds = model.datasets[dataset_name]
    return exp.Table(
        this=exp.to_identifier(ds.table_name),
        db=exp.to_identifier(ds.schema_name),
        catalog=exp.to_identifier(ds.catalog_slug),
        alias=exp.TableAlias(this=exp.to_identifier(ds.name)),
    )


def _time_dimension(model: LoadedModel, metrics: list[Metric], base: str):
    """The time axis for this query, or a refusal explaining why there isn't one.

    A metric's own binding wins. Where several metrics are asked for together they
    must agree, because two metrics measured on different dates cannot share one
    period column without one of them being wrong.
    """
    named = {m.time_dimension for m in metrics if m.time_dimension}
    if len(named) > 1:
        raise SemanticError(
            "These metrics are measured on different dates ("
            + ", ".join(sorted(named))
            + "), so they cannot share one time axis. Ask for them separately."
        )
    if named:
        return model.dimensions.get(next(iter(named)))
    return model.default_time_dimension(base)


def compile_metric_query(
    model: LoadedModel,
    query: MetricQuery,
    *,
    today: date | None = None,
) -> CompiledQuery:
    """Compile a semantic request into DuckDB SQL.

    Raises :class:`SemanticError` rather than producing an approximation. Every
    refusal names what would have worked instead.
    """
    if not query.metrics:
        raise SemanticError("No metric was requested.", alternatives=sorted(model.metrics))

    metrics: list[Metric] = []
    for name in query.metrics:
        metric = model.metrics.get(name)
        if metric is None:
            raise SemanticError(
                f"{model.slug!r} has no metric called {name!r}.",
                alternatives=sorted(model.metrics),
            )
        if metric.status == "deprecated":
            raise SemanticError(
                f"Metric {name!r} is deprecated and must not be used for new answers"
                + (f": {metric.description}" if metric.description else ".")
            )
        metrics.append(metric)

    bases = {m.dataset for m in metrics}
    if len(bases) > 1:
        raise SemanticError(
            "These metrics are defined on different datasets ("
            + ", ".join(sorted(bases))
            + "), so combining them in one query would change what each one counts. "
            "Ask for them separately."
        )
    base = next(iter(bases))

    select_exprs: list[exp.Expression] = []
    group_exprs: list[exp.Expression] = []
    paths: list = []
    definitions: list[dict] = []
    warnings: list[str] = []

    # ── Dimensions ────────────────────────────────────────────────────────────
    for name in query.dimensions:
        dim = model.dimensions.get(name)
        if dim is None:
            raise SemanticError(
                f"{model.slug!r} has no dimension called {name!r}.",
                alternatives=sorted(model.dimensions),
            )
        path = resolve_path(model, base, dim.dataset)
        paths.append(path)
        node = _scalar(dim.expr, alias=dim.dataset, what=f"dimension {dim.name!r} expression")
        group_exprs.append(node.copy())
        select_exprs.append(exp.alias_(node, exp.to_identifier(dim.name)))
        definitions.append(
            {
                "kind": "dimension",
                "model": model.slug,
                "name": dim.name,
                "label": dim.label,
                "description": dim.description,
                "dataset": dim.dataset,
            }
        )

    # ── Time axis ─────────────────────────────────────────────────────────────
    time_dim = None
    if query.grain or query.time_range:
        time_dim = _time_dimension(model, metrics, base)
        if time_dim is None:
            raise SemanticError(
                f"No time dimension is defined for {base!r}, so this query cannot be "
                "filtered or grouped by time. Define one, or ask without a time window.",
                alternatives=sorted(d.name for d in model.dimensions.values() if d.kind == "time"),
            )
        if time_dim.kind != "time":
            raise SemanticError(f"{time_dim.name!r} is not a time dimension.")
        paths.append(resolve_path(model, base, time_dim.dataset))

    time_expr = None
    if time_dim is not None:
        time_expr = _scalar(
            time_dim.expr, alias=time_dim.dataset, what=f"dimension {time_dim.name!r} expression"
        )

    if query.grain:
        if query.grain not in time_dim.time_grains:
            raise SemanticError(
                f"{time_dim.name!r} does not support a {query.grain!r} grain.",
                alternatives=list(time_dim.time_grains),
            )
        truncated = exp.func("DATE_TRUNC", exp.Literal.string(query.grain), time_expr.copy())
        group_exprs.append(truncated.copy())
        select_exprs.append(exp.alias_(truncated, exp.to_identifier(query.grain)))

    # ── Metrics ───────────────────────────────────────────────────────────────
    for metric in metrics:
        select_exprs.append(exp.alias_(_aggregate(metric, base), exp.to_identifier(metric.name)))
        definitions.append(
            {
                "kind": "metric",
                "model": model.slug,
                "name": metric.name,
                "label": metric.label,
                "description": metric.description,
                "expression": metric.render(),
                "dataset": metric.dataset,
                "time_dimension": metric.time_dimension,
                "caveat": metric.caveat,
                "status": metric.status,
            }
        )
        if metric.caveat:
            warnings.append(f"{metric.label}: {metric.caveat}")

    # ── Predicates ────────────────────────────────────────────────────────────
    conditions: list[exp.Expression] = []

    if query.time_range is not None:
        start, end = resolve_window(query.time_range, today=today or date.today())
        conditions.append(
            exp.GTE(
                this=time_expr.copy(),
                expression=exp.cast(exp.Literal.string(start.isoformat()), "DATE"),
            )
        )
        conditions.append(
            exp.LT(
                this=time_expr.copy(),
                expression=exp.cast(exp.Literal.string(end.isoformat()), "DATE"),
            )
        )
        definitions.append(
            {
                "kind": "time_range",
                "model": model.slug,
                "name": time_dim.name,
                "start": start.isoformat(),
                "end": end.isoformat(),
            }
        )

    for filt in query.filters:
        dim = model.dimensions.get(filt.dimension)
        if dim is None:
            raise SemanticError(
                f"{model.slug!r} has no dimension called {filt.dimension!r} to filter on.",
                alternatives=sorted(model.dimensions),
            )
        paths.append(resolve_path(model, base, dim.dataset))
        node = _scalar(dim.expr, alias=dim.dataset, what=f"dimension {dim.name!r} expression")
        conditions.append(_predicate(node, filt))

    # ── Assemble ──────────────────────────────────────────────────────────────
    statement = exp.Select(expressions=select_exprs).from_(_table(model, base))

    for rel in merge_paths(paths):
        if not rel.join_columns:
            raise SemanticError(
                f"Relationship {rel.name!r} declares no join columns, so it cannot be used."
            )
        on = None
        for left_col, right_col in rel.join_columns:
            eq = exp.EQ(
                this=exp.column(left_col, table=rel.left),
                expression=exp.column(right_col, table=rel.right),
            )
            on = eq if on is None else exp.And(this=on, expression=eq)
        # LEFT, always: an INNER join here would silently drop fact rows whose
        # lookup is missing, turning a data-quality problem into a wrong total.
        statement = statement.join(_table(model, rel.right), on=on, join_type="LEFT")

    if conditions:
        where = conditions[0]
        for extra in conditions[1:]:
            where = exp.And(this=where, expression=extra)
        statement = statement.where(where)

    if group_exprs:
        statement = statement.group_by(*group_exprs)

    selected = {e.alias for e in select_exprs if isinstance(e, exp.Alias)}
    for term in query.order_by:
        if term.field not in selected:
            raise SemanticError(
                f"Cannot order by {term.field!r} because it is not in the result.",
                alternatives=sorted(selected),
            )
        statement = statement.order_by(
            exp.Ordered(this=exp.column(term.field), desc=term.descending)
        )

    limit = query.limit if query.limit is not None else DEFAULT_LIMIT
    if limit < 1:
        raise SemanticError("A row limit must be at least 1.")
    if limit > MAX_LIMIT:
        warnings.append(f"Row limit reduced from {limit} to {MAX_LIMIT}.")
        limit = MAX_LIMIT
    statement = statement.limit(limit)

    return CompiledQuery(
        sql=statement.sql(dialect="duckdb", pretty=True),
        definitions_used=definitions,
        warnings=warnings,
    )


def legal_dimensions(model: LoadedModel, metric_name: str) -> list[str]:
    """The dimensions a given metric can actually be sliced by.

    The answer to "which combinations are valid?", which turns the assistant's
    dimension choice into a lookup rather than a gamble. That is most of the
    value of declaring relationships at all — and it is what lets a UI offer only
    the combinations that will compile, instead of letting somebody build a query
    the compiler then refuses.
    """
    metric = model.metrics.get(metric_name)
    if metric is None:
        raise SemanticError(
            f"{model.slug!r} has no metric called {metric_name!r}.",
            alternatives=sorted(model.metrics),
        )
    allowed = {metric.dataset} | set(reachable(model, metric.dataset))
    return sorted(d.name for d in model.dimensions.values() if d.dataset in allowed)
