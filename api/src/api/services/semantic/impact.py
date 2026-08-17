"""Which definitions depend on this table, or this column?

The direction lineage cannot answer. Lineage knows that ``orders`` feeds
``daily_revenue``; it does not know that dropping ``orders.total_amount`` breaks
the published definition of revenue that four teams quote in meetings. That
dependency lives here, and it is exact rather than inferred — a metric names its
column, so the blast radius is a lookup, not an estimate.

Used in two places, both of them the moment before something goes wrong: the
table detail page, and the confirmation dialog for dropping a table.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import sqlglot
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlglot import exp

from api.models.semantic import (
    SemanticDataset,
    SemanticDimension,
    SemanticMetric,
    SemanticModel,
)


@dataclass(frozen=True)
class Dependent:
    """One semantic definition that depends on the table being asked about."""

    kind: str
    model_slug: str
    model_name: str
    model_status: str
    name: str
    label: str
    status: str
    dataset: str
    columns: tuple[str, ...]

    def as_dict(self) -> dict:
        return {
            "kind": self.kind,
            "model": self.model_slug,
            "model_name": self.model_name,
            "model_status": self.model_status,
            "name": self.name,
            "label": self.label,
            "status": self.status,
            "dataset": self.dataset,
            "columns": list(self.columns),
        }


def _referenced(expr: str | None) -> tuple[str, ...]:
    """Column names an expression reads, lowercased. Empty when it will not parse."""
    if not expr:
        return ()
    try:
        tree = sqlglot.parse_one(expr, read="duckdb")
    except Exception:
        return ()
    if tree is None:
        return ()
    return tuple(sorted({c.name.lower() for c in tree.find_all(exp.Column)}))


async def dependents_for_table(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    catalog_id: uuid.UUID,
    schema_name: str,
    table_name: str,
    column: str | None = None,
) -> list[Dependent]:
    """Every metric and dimension bound to one physical table.

    Scoped to one workspace: a definition in another workspace is somebody else's
    business, and naming it here would leak that workspace's vocabulary to a
    caller who is not a member of it.
    """
    rows = (
        await db.execute(
            select(SemanticDataset, SemanticModel)
            .join(SemanticModel, SemanticModel.id == SemanticDataset.model_id)
            .where(
                SemanticModel.workspace_id == workspace_id,
                SemanticDataset.catalog_id == catalog_id,
                SemanticDataset.schema_name == schema_name,
                SemanticDataset.table_name == table_name,
            )
        )
    ).all()
    if not rows:
        return []

    datasets = {ds.id: (ds, model) for ds, model in rows}
    wanted = column.lower() if column else None

    metrics = list(
        (
            await db.execute(select(SemanticMetric).where(SemanticMetric.dataset_id.in_(datasets)))
        ).scalars()
    )
    dimensions = list(
        (
            await db.execute(
                select(SemanticDimension).where(SemanticDimension.dataset_id.in_(datasets))
            )
        ).scalars()
    )

    found: list[Dependent] = []

    for metric in metrics:
        ds, model = datasets[metric.dataset_id]
        columns = tuple(sorted(set(_referenced(metric.expr) + _referenced(metric.filter))))
        if wanted and wanted not in columns:
            continue
        found.append(
            Dependent(
                kind="metric",
                model_slug=model.slug,
                model_name=model.name,
                model_status=model.status,
                name=metric.name,
                label=metric.display_name or metric.name,
                status=metric.status,
                dataset=ds.name,
                columns=columns,
            )
        )

    for dim in dimensions:
        ds, model = datasets[dim.dataset_id]
        columns = _referenced(dim.expr)
        if wanted and wanted not in columns:
            continue
        found.append(
            Dependent(
                kind="dimension",
                model_slug=model.slug,
                model_name=model.name,
                model_status=model.status,
                name=dim.name,
                label=dim.display_name or dim.name,
                status=model.status,
                dataset=ds.name,
                columns=columns,
            )
        )

    # Published first: those are the ones somebody is quoting in a meeting, so
    # they are what a person about to drop a column needs to see at the top.
    found.sort(key=lambda d: (d.model_status != "published", d.kind != "metric", d.name))
    return found


async def mark_bindings_broken(
    db: AsyncSession,
    *,
    catalog_id: uuid.UUID,
    schema_name: str,
    table_name: str | None = None,
    detail: str,
) -> int:
    """Mark datasets bound to a dropped table or schema as broken.

    Deliberately *not* a delete, which is what the drop path does for grants and
    lineage. Those describe the table; a semantic definition describes the
    business, and it outlives the table it happened to be bound to. Deleting it
    would silently discard somebody's work and make the assistant fall back to
    inventing its own revenue calculation — the exact failure this subsystem
    exists to prevent. Marking it broken keeps it visible, keeps it withheld from
    the assistant, and leaves it repairable by rebinding.
    """
    stmt = select(SemanticDataset).where(
        SemanticDataset.catalog_id == catalog_id,
        SemanticDataset.schema_name == schema_name,
    )
    if table_name is not None:
        stmt = stmt.where(SemanticDataset.table_name == table_name)

    affected = list((await db.execute(stmt)).scalars())
    for ds in affected:
        ds.validation_state = "broken"
        ds.validation_detail = detail
    if affected:
        await db.flush()
    return len(affected)


async def mark_bindings_unchecked(
    db: AsyncSession,
    *,
    catalog_id: uuid.UUID,
    schema_name: str,
    table_name: str,
) -> int:
    """Retract a previous verdict without claiming a new one.

    Used when a table was dropped and recreated under the same name. The columns
    an expression references may or may not still be there, and the honest state
    is "nobody has looked since this changed" — which is exactly what
    ``unchecked`` means, and what makes the next read revalidate instead of
    trusting a verdict about a table that no longer exists.
    """
    affected = list(
        (
            await db.execute(
                select(SemanticDataset).where(
                    SemanticDataset.catalog_id == catalog_id,
                    SemanticDataset.schema_name == schema_name,
                    SemanticDataset.table_name == table_name,
                    SemanticDataset.validation_state != "unchecked",
                )
            )
        ).scalars()
    )
    for ds in affected:
        ds.validation_state = "unchecked"
        ds.validation_detail = None
    if affected:
        await db.flush()
    return len(affected)


async def rekey_bindings(
    db: AsyncSession,
    *,
    catalog_id: uuid.UUID,
    old_schema: str,
    old_table: str,
    new_schema: str,
    new_table: str,
) -> int:
    """Follow a renamed table, so its definitions survive the rename.

    Called from the same seam as ``rekey_table_lineage``, driven by the Iceberg
    table id rather than the name — which is the only way to tell a rename from a
    drop-and-recreate, and they need opposite treatment.
    """
    affected = list(
        (
            await db.execute(
                select(SemanticDataset).where(
                    SemanticDataset.catalog_id == catalog_id,
                    SemanticDataset.schema_name == old_schema,
                    SemanticDataset.table_name == old_table,
                )
            )
        ).scalars()
    )
    for ds in affected:
        ds.schema_name = new_schema
        ds.table_name = new_table
    if affected:
        await db.flush()
    return len(affected)
