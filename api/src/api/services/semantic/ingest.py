"""The single write path for imported semantic definitions.

Everything an adapter produces lands here, so identity, replacement and
reconciliation are decided in one module regardless of which producer sent it —
the same shape ``lineage.ingest`` uses, for the same reason.

Two rules make this much simpler than the lineage equivalent, and both come from
one decision: **a model belongs to exactly one provider.**

*Replacement is wholesale.* An imported model's children are deleted and rewritten
from the artifact rather than diffed. The artifact is the source of truth for that
model, so a diff could only ever produce the same result more slowly and with more
ways to be wrong. Model identity survives (same row, same id, same URL), which is
what a diff would have been protecting.

*There is no merge.* Because a model has one owner, an import can never collide
with a hand-authored definition — it can only collide with an earlier version of
itself. The API refuses to edit an imported model, so the conflict this would
otherwise resolve does not arise.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.semantic import (
    NATIVE_PROVIDER,
    SemanticDataset,
    SemanticDimension,
    SemanticMetric,
    SemanticModel,
    SemanticRelationship,
)


@dataclass(frozen=True)
class CanonicalDataset:
    name: str
    catalog_id: uuid.UUID
    schema_name: str
    table_name: str
    description: str | None = None
    synonyms: tuple[str, ...] = ()
    primary_key: tuple[str, ...] = ()


@dataclass(frozen=True)
class CanonicalDimension:
    name: str
    dataset: str
    expr: str
    kind: str = "categorical"
    display_name: str | None = None
    description: str | None = None
    synonyms: tuple[str, ...] = ()
    data_type: str | None = None
    time_grains: tuple[str, ...] = ()
    is_default_time: bool = False
    sample_values: tuple[str, ...] = ()


@dataclass(frozen=True)
class CanonicalMetric:
    name: str
    dataset: str
    agg: str
    expr: str | None = None
    filter: str | None = None
    time_dimension: str | None = None
    display_name: str | None = None
    description: str | None = None
    synonyms: tuple[str, ...] = ()
    caveat: str | None = None


@dataclass(frozen=True)
class CanonicalRelationship:
    name: str
    left: str
    right: str
    join_columns: tuple[tuple[str, str], ...]
    cardinality: str = "many_to_one"


@dataclass(frozen=True)
class CanonicalModel:
    """One subject area, in the shape every provider normalises into."""

    slug: str
    name: str
    description: str | None = None
    datasets: tuple[CanonicalDataset, ...] = ()
    dimensions: tuple[CanonicalDimension, ...] = ()
    metrics: tuple[CanonicalMetric, ...] = ()
    relationships: tuple[CanonicalRelationship, ...] = ()


@dataclass
class IngestResult:
    created: int = 0
    updated: int = 0
    removed: int = 0
    skipped: list = field(default_factory=list)


async def _clear_children(db: AsyncSession, model_id: uuid.UUID) -> None:
    # Metrics before dimensions: a metric points at its time dimension, and on
    # SQLite the FK is not enforced anyway, so ordering here is about being
    # correct on Postgres rather than about what the test suite would catch.
    for table in (
        SemanticMetric,
        SemanticRelationship,
        SemanticDimension,
        SemanticDataset,
    ):
        await db.execute(delete(table).where(table.model_id == model_id))


async def upsert_models(
    db: AsyncSession,
    models: list[CanonicalModel],
    *,
    provider: str,
    provider_run_id: str | None,
    workspace_id: uuid.UUID,
    owner_id: uuid.UUID | None = None,
) -> IngestResult:
    """Write one provider's models into a workspace, replacing what it wrote before."""
    if provider == NATIVE_PROVIDER:
        raise ValueError(f"{NATIVE_PROVIDER!r} is reserved for definitions authored here.")

    result = IngestResult()
    now = datetime.now(UTC)

    for canonical in models:
        existing = (
            await db.execute(
                select(SemanticModel).where(
                    SemanticModel.workspace_id == workspace_id,
                    SemanticModel.slug == canonical.slug,
                )
            )
        ).scalar_one_or_none()

        if existing is not None and existing.provider != provider:
            # A slug already owned by somebody else — a hand-authored model, or
            # another importer. Refusing keeps "one model, one owner" true, and
            # reporting it means the collision is visible rather than silent.
            result.skipped.append(
                {
                    "ref": canonical.slug,
                    "reason": "slug_owned_by_other_provider",
                    "detail": (
                        f"{canonical.slug!r} already exists and is owned by {existing.provider!r}."
                    ),
                }
            )
            continue

        if existing is None:
            model = SemanticModel(
                workspace_id=workspace_id,
                slug=canonical.slug,
                name=canonical.name,
                description=canonical.description,
                provider=provider,
                provider_run_id=provider_run_id,
                owner_id=owner_id,
                # Imported definitions arrive as drafts. An import is a
                # publishing act by a pipeline, not by a person, and the whole
                # point of the published gate is that a person decided.
                status="draft",
                last_seen_at=now,
            )
            db.add(model)
            await db.flush()
            result.created += 1
        else:
            model = existing
            model.name = canonical.name
            model.description = canonical.description
            model.provider_run_id = provider_run_id
            model.updated_at = now
            model.last_seen_at = now
            await _clear_children(db, model.id)
            await db.flush()
            result.updated += 1

        dataset_ids: dict[str, uuid.UUID] = {}
        for ds in canonical.datasets:
            row = SemanticDataset(
                model_id=model.id,
                name=ds.name,
                description=ds.description,
                synonyms=list(ds.synonyms),
                catalog_id=ds.catalog_id,
                schema_name=ds.schema_name,
                table_name=ds.table_name,
                primary_key=list(ds.primary_key),
            )
            db.add(row)
            await db.flush()
            dataset_ids[ds.name] = row.id

        dimension_ids: dict[str, uuid.UUID] = {}
        for dim in canonical.dimensions:
            parent = dataset_ids.get(dim.dataset)
            if parent is None:
                result.skipped.append(
                    {
                        "ref": f"{canonical.slug}.{dim.name}",
                        "reason": "unknown_dataset",
                        "detail": f"No dataset called {dim.dataset!r} in this model.",
                    }
                )
                continue
            row = SemanticDimension(
                model_id=model.id,
                dataset_id=parent,
                name=dim.name,
                display_name=dim.display_name,
                description=dim.description,
                synonyms=list(dim.synonyms),
                kind=dim.kind,
                expr=dim.expr,
                data_type=dim.data_type,
                time_grains=list(dim.time_grains),
                is_default_time=dim.is_default_time,
                sample_values=list(dim.sample_values),
            )
            db.add(row)
            await db.flush()
            dimension_ids[dim.name] = row.id

        for metric in canonical.metrics:
            parent = dataset_ids.get(metric.dataset)
            if parent is None:
                result.skipped.append(
                    {
                        "ref": f"{canonical.slug}.{metric.name}",
                        "reason": "unknown_dataset",
                        "detail": f"No dataset called {metric.dataset!r} in this model.",
                    }
                )
                continue
            axis = dimension_ids.get(metric.time_dimension) if metric.time_dimension else None
            if metric.time_dimension and axis is None:
                result.skipped.append(
                    {
                        "ref": f"{canonical.slug}.{metric.name}",
                        "reason": "unknown_time_dimension",
                        "detail": (
                            f"{metric.time_dimension!r} is not a dimension in this model; the "
                            "metric was imported without a time axis."
                        ),
                    }
                )
            db.add(
                SemanticMetric(
                    model_id=model.id,
                    dataset_id=parent,
                    name=metric.name,
                    display_name=metric.display_name,
                    description=metric.description,
                    synonyms=list(metric.synonyms),
                    agg=metric.agg,
                    expr=metric.expr,
                    filter=metric.filter,
                    time_dimension_id=axis,
                    caveat=metric.caveat,
                    owner_id=owner_id,
                )
            )

        for rel in canonical.relationships:
            left = dataset_ids.get(rel.left)
            right = dataset_ids.get(rel.right)
            if left is None or right is None:
                result.skipped.append(
                    {
                        "ref": f"{canonical.slug}.{rel.name}",
                        "reason": "unknown_dataset",
                        "detail": f"{rel.left!r} -> {rel.right!r} names a dataset this model "
                        "does not define.",
                    }
                )
                continue
            db.add(
                SemanticRelationship(
                    model_id=model.id,
                    name=rel.name,
                    left_dataset_id=left,
                    right_dataset_id=right,
                    join_columns=[{"left": a, "right": b} for a, b in rel.join_columns],
                    cardinality=rel.cardinality,
                )
            )

        await db.flush()

    return result


async def reconcile_provider_run(
    db: AsyncSession,
    *,
    provider: str,
    workspace_id: uuid.UUID,
    model_slugs: set[str],
) -> int:
    """Retire models this provider used to publish and no longer does.

    Scoped to one provider, and only ever run when the caller says the payload is
    the complete set — a partial publish must not delete the models it simply did
    not mention.
    """
    stale = list(
        (
            await db.execute(
                select(SemanticModel).where(
                    SemanticModel.workspace_id == workspace_id,
                    SemanticModel.provider == provider,
                    SemanticModel.slug.notin_(model_slugs) if model_slugs else True,
                )
            )
        ).scalars()
    )
    for model in stale:
        await _clear_children(db, model.id)
        await db.delete(model)
    if stale:
        await db.flush()
    return len(stale)


async def purge_provider(db: AsyncSession, *, provider: str, workspace_id: uuid.UUID) -> int:
    """Remove everything one provider published into a workspace."""
    if provider == NATIVE_PROVIDER:
        raise ValueError(
            f"{NATIVE_PROVIDER!r} models are authored here and are deleted individually."
        )
    return await reconcile_provider_run(
        db, provider=provider, workspace_id=workspace_id, model_slugs=set()
    )
