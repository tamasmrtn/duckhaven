"""Does this model still describe reality?

A semantic definition is a claim about a table — that a column exists, that a key
is unique, that an expression parses. Every one of those can stop being true
without anything erroring: somebody drops a column, renames a table, or changes a
type, and the metric goes on looking perfectly fine right up until it produces a
wrong number or a confusing failure deep inside a query.

So bindings are checked against **live Polaris**, never against a cached copy of
the schema (I3), and the outcome is recorded per object as a
``validation_state``. The three states are distinct on purpose: ``ok`` means
checked and holding, ``broken`` means checked and wrong, and ``unchecked`` means
nothing has looked since something changed — which is not the same as fine, and
which the compiler treats as a reason to revalidate rather than as permission.

Failing loudly is the whole point. A ``broken`` metric is withheld from the
assistant and refused by the compiler, so the assistant says "that definition is
broken" instead of quietly inventing its own SQL for revenue.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

import sqlglot
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlglot import exp

from api.models.semantic import (
    SemanticDataset,
    SemanticDimension,
    SemanticMetric,
    SemanticModel,
    SemanticRelationship,
)
from api.services.polaris import PolarisError
from api.services.semantic.model import AGGREGATIONS, TIME_GRAINS

# Thresholds, not limits. Both researched platforms bound a model far below what
# is technically storable — Snowflake recommends ten tables per semantic view,
# Databricks five per Genie space — because a small model is itself the accuracy
# mechanism. The right fix is to split the model, which only an author can do, so
# this warns rather than refuses.
MAX_DATASETS_ADVISED = 10
MAX_FIELDS_ADVISED = 60


@dataclass
class ValidationReport:
    """What was checked and what did not hold."""

    ok: bool = True
    errors: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    checked_at: datetime | None = None

    def fail(self, kind: str, name: str, detail: str) -> None:
        self.ok = False
        self.errors.append({"kind": kind, "name": name, "detail": detail})


def _columns_of(expr: str) -> set[str] | None:
    """Bare column names an expression references, or None if it will not parse."""
    try:
        statements = sqlglot.parse(expr, read="duckdb")
    except Exception:
        return None
    if len(statements) != 1 or statements[0] is None:
        return None
    tree = statements[0]
    if isinstance(tree, exp.Select | exp.Union | exp.Subquery) or tree.find(exp.Select):
        return None
    return {c.name for c in tree.find_all(exp.Column)}


def _check_expression(
    report: ValidationReport,
    *,
    kind: str,
    name: str,
    label: str,
    expr: str,
    available: set[str],
) -> str | None:
    """Validate one author-written expression. Returns a detail string on failure."""
    referenced = _columns_of(expr)
    if referenced is None:
        detail = f"{label} is not a valid scalar SQL expression."
        report.fail(kind, name, detail)
        return detail
    missing = sorted(c for c in referenced if c.lower() not in available)
    if missing:
        detail = f"{label} references column(s) that no longer exist: {', '.join(missing)}."
        report.fail(kind, name, detail)
        return detail
    return None


async def validate_model(
    db: AsyncSession,
    polaris,
    model: SemanticModel,
    *,
    catalog_names: dict[uuid.UUID, str],
) -> ValidationReport:
    """Check every binding in a model and persist the outcome per object.

    ``catalog_names`` maps catalog id to its **Polaris** warehouse name, which is
    what the REST catalog is addressed by — not the DuckHaven slug, which is a
    display and SQL-alias concern.
    """
    report = ValidationReport(checked_at=datetime.now(UTC))

    datasets = list(
        (
            await db.execute(select(SemanticDataset).where(SemanticDataset.model_id == model.id))
        ).scalars()
    )
    dimensions = list(
        (
            await db.execute(
                select(SemanticDimension).where(SemanticDimension.model_id == model.id)
            )
        ).scalars()
    )
    metrics = list(
        (
            await db.execute(select(SemanticMetric).where(SemanticMetric.model_id == model.id))
        ).scalars()
    )
    relationships = list(
        (
            await db.execute(
                select(SemanticRelationship).where(SemanticRelationship.model_id == model.id)
            )
        ).scalars()
    )

    # ── Datasets: does the physical table still exist? ────────────────────────
    columns_by_dataset: dict[uuid.UUID, set[str]] = {}
    for ds in datasets:
        polaris_name = catalog_names.get(ds.catalog_id)
        if polaris_name is None:
            ds.validation_state = "broken"
            ds.validation_detail = "The catalog this dataset binds to is no longer available."
            report.fail("dataset", ds.name, ds.validation_detail)
            continue
        try:
            table = await polaris.get_table(polaris_name, ds.schema_name, ds.table_name)
        except PolarisError as exc:
            ds.validation_state = "broken"
            ds.validation_detail = (
                f"{ds.schema_name}.{ds.table_name} could not be read from the catalog: {exc}"
            )
            report.fail("dataset", ds.name, ds.validation_detail)
            continue

        available = {c.name.lower() for c in table.columns}
        columns_by_dataset[ds.id] = available

        missing_key = sorted(c for c in (ds.primary_key or []) if c.lower() not in available)
        if missing_key:
            ds.validation_state = "broken"
            ds.validation_detail = (
                f"Primary key column(s) missing from {ds.table_name}: {', '.join(missing_key)}."
            )
            report.fail("dataset", ds.name, ds.validation_detail)
            continue

        ds.validation_state = "ok"
        ds.validation_detail = None
        ds.last_validated_at = report.checked_at

    # ── Dimensions ────────────────────────────────────────────────────────────
    for dim in dimensions:
        available = columns_by_dataset.get(dim.dataset_id)
        if available is None:
            dim.validation_state = "broken"
            dim.validation_detail = "The dataset this dimension belongs to did not validate."
            report.fail("dimension", dim.name, dim.validation_detail)
            continue

        detail = _check_expression(
            report,
            kind="dimension",
            name=dim.name,
            label=f"Dimension {dim.name!r}",
            expr=dim.expr,
            available=available,
        )
        if detail:
            dim.validation_state = "broken"
            dim.validation_detail = detail
            continue

        bad_grains = sorted(set(dim.time_grains or []) - set(TIME_GRAINS))
        if bad_grains:
            dim.validation_state = "broken"
            dim.validation_detail = f"Unsupported time grain(s): {', '.join(bad_grains)}."
            report.fail("dimension", dim.name, dim.validation_detail)
            continue
        if dim.kind != "time" and dim.time_grains:
            report.warnings.append(
                f"Dimension {dim.name!r} lists time grains but is not a time dimension; "
                "the grains will be ignored."
            )

        dim.validation_state = "ok"
        dim.validation_detail = None

    # ── Metrics ───────────────────────────────────────────────────────────────
    time_dim_ids = {d.id for d in dimensions if d.kind == "time"}
    for metric in metrics:
        available = columns_by_dataset.get(metric.dataset_id)
        if available is None:
            metric.validation_state = "broken"
            metric.validation_detail = "The dataset this metric belongs to did not validate."
            report.fail("metric", metric.name, metric.validation_detail)
            continue

        if metric.agg not in AGGREGATIONS:
            metric.validation_state = "broken"
            metric.validation_detail = f"Unsupported aggregation {metric.agg!r}."
            report.fail("metric", metric.name, metric.validation_detail)
            continue

        if metric.agg != "count" and not metric.expr:
            metric.validation_state = "broken"
            metric.validation_detail = (
                f"{metric.agg} needs an expression to aggregate; only count may omit one."
            )
            report.fail("metric", metric.name, metric.validation_detail)
            continue

        failed = False
        for expr, label in ((metric.expr, "expression"), (metric.filter, "filter")):
            if not expr:
                continue
            detail = _check_expression(
                report,
                kind="metric",
                name=metric.name,
                label=f"Metric {metric.name!r} {label}",
                expr=expr,
                available=available,
            )
            if detail:
                metric.validation_state = "broken"
                metric.validation_detail = detail
                failed = True
                break
        if failed:
            continue

        if metric.time_dimension_id and metric.time_dimension_id not in time_dim_ids:
            metric.validation_state = "broken"
            metric.validation_detail = (
                "This metric is bound to a dimension that is not a time dimension."
            )
            report.fail("metric", metric.name, metric.validation_detail)
            continue

        if metric.time_dimension_id is None:
            # Not an error — a metric can be legitimately timeless — but the most
            # expensive wrong answer in analytics is a time filter on the wrong
            # column, so an unbound metric is worth saying out loud.
            report.warnings.append(
                f"Metric {metric.name!r} is not bound to a time dimension, so it cannot be "
                "filtered or grouped by time unless its dataset has exactly one."
            )

        metric.validation_state = "ok"
        metric.validation_detail = None

    # ── Relationships: is the claimed uniqueness real? ────────────────────────
    by_id = {d.id: d for d in datasets}
    for rel in relationships:
        left = by_id.get(rel.left_dataset_id)
        right = by_id.get(rel.right_dataset_id)
        if left is None or right is None:
            rel.validation_state = "broken"
            rel.validation_detail = "This relationship points at a dataset that no longer exists."
            report.fail("relationship", rel.name, rel.validation_detail)
            continue

        pairs = [
            (p.get("left"), p.get("right")) for p in (rel.join_columns or []) if isinstance(p, dict)
        ]
        if not pairs or any(not lft or not rgt for lft, rgt in pairs):
            rel.validation_state = "broken"
            rel.validation_detail = "This relationship declares no usable join columns."
            report.fail("relationship", rel.name, rel.validation_detail)
            continue

        left_cols = columns_by_dataset.get(left.id, set())
        right_cols = columns_by_dataset.get(right.id, set())
        missing = [f"{left.name}.{lft}" for lft, _ in pairs if lft.lower() not in left_cols] + [
            f"{right.name}.{rgt}" for _, rgt in pairs if rgt.lower() not in right_cols
        ]
        if missing:
            rel.validation_state = "broken"
            rel.validation_detail = f"Join column(s) missing: {', '.join(sorted(missing))}."
            report.fail("relationship", rel.name, rel.validation_detail)
            continue

        # The fan-out check. `many_to_one` asserts the right-hand side is unique
        # on the joined columns; if those are not its primary key, the assertion is
        # unbacked and the join will multiply fact rows — inflating every metric
        # that crosses it, with no error anywhere.
        key = {c.lower() for c in (right.primary_key or [])}
        joined = {rgt.lower() for _, rgt in pairs}
        if not key:
            rel.validation_state = "broken"
            rel.validation_detail = (
                f"{right.name!r} declares no primary key, so it cannot be the unique side "
                "of a join. Declare its key, or the join may multiply rows and inflate "
                "every metric that crosses it."
            )
            report.fail("relationship", rel.name, rel.validation_detail)
            continue
        if joined != key:
            rel.validation_state = "broken"
            rel.validation_detail = (
                f"This joins {right.name!r} on {', '.join(sorted(joined))}, which is not its "
                f"primary key ({', '.join(sorted(key))}). That does not guarantee one match "
                "per row, so the join may multiply rows and inflate every metric."
            )
            report.fail("relationship", rel.name, rel.validation_detail)
            continue

        rel.validation_state = "ok"
        rel.validation_detail = None

    # ── Size advice ───────────────────────────────────────────────────────────
    if len(datasets) > MAX_DATASETS_ADVISED:
        report.warnings.append(
            f"This model binds {len(datasets)} datasets. Models above ~{MAX_DATASETS_ADVISED} "
            "are noticeably less reliable for the assistant; consider splitting it by subject."
        )
    fields = len(dimensions) + len(metrics)
    if fields > MAX_FIELDS_ADVISED:
        report.warnings.append(
            f"This model defines {fields} dimensions and metrics. Above ~{MAX_FIELDS_ADVISED} "
            "the assistant has trouble choosing between them; consider splitting it."
        )

    model.updated_at = report.checked_at
    await db.flush()
    return report
