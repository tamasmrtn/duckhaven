"""The in-memory shape of a loaded semantic model.

Deliberately plain frozen dataclasses rather than the ORM rows. The compiler is
the part of this subsystem most worth testing exhaustively — it is what stands
between a question and a number — and a compiler that takes a database session
can only be tested with a database. Loading once into this shape means every
compile case is a pure function call.

It also draws the trust boundary in one place: :func:`load_model` is where
``draft`` and ``broken`` definitions are filtered out, so nothing downstream has
to remember to check.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.catalog import Catalog
from api.models.semantic import (
    SemanticDataset,
    SemanticDimension,
    SemanticMetric,
    SemanticModel,
    SemanticRelationship,
)

# The grains a time dimension may offer. Anything outside this is rejected at
# authoring time rather than passed through to the engine, so "fortnightly" fails
# where somebody can fix it instead of where somebody is waiting for an answer.
TIME_GRAINS = ("day", "week", "month", "quarter", "year")

AGGREGATIONS = ("sum", "count", "count_distinct", "avg", "min", "max")


@dataclass(frozen=True)
class Dataset:
    """A logical table and the physical one it is bound to."""

    id: uuid.UUID
    name: str
    description: str | None
    synonyms: tuple[str, ...]
    catalog_id: uuid.UUID
    catalog_slug: str
    schema_name: str
    table_name: str
    primary_key: tuple[str, ...]
    validation_state: str

    @property
    def qualified(self) -> str:
        return f"{self.catalog_slug}.{self.schema_name}.{self.table_name}"


@dataclass(frozen=True)
class Dimension:
    """One way to slice: a categorical attribute or a time axis."""

    id: uuid.UUID
    name: str
    display_name: str | None
    description: str | None
    synonyms: tuple[str, ...]
    kind: str
    expr: str
    data_type: str | None
    time_grains: tuple[str, ...]
    is_default_time: bool
    sample_values: tuple[str, ...]
    dataset: str
    validation_state: str

    @property
    def label(self) -> str:
        return self.display_name or self.name

    @property
    def qualified_name(self) -> str:
        return f"{self.dataset}.{self.name}"


@dataclass(frozen=True)
class Metric:
    """An authoritative business measure and how it is computed."""

    id: uuid.UUID
    name: str
    display_name: str | None
    description: str | None
    synonyms: tuple[str, ...]
    agg: str
    expr: str | None
    filter: str | None
    time_dimension: str | None
    caveat: str | None
    status: str
    dataset: str
    validation_state: str

    @property
    def label(self) -> str:
        return self.display_name or self.name

    def render(self) -> str:
        """A human-readable rendering of the calculation.

        Shown in ``explain_metric`` and in the UI. Not the compiled SQL — that
        comes from the compiler with the joins and filters resolved — but enough
        that "how is revenue calculated?" has a real answer instead of a
        paraphrase invented from column names.
        """
        inner = self.expr or "*"
        if self.agg == "count_distinct":
            call = f"COUNT(DISTINCT {inner})"
        else:
            call = f"{self.agg.upper()}({inner})"
        if self.filter:
            call = f"{call} FILTER (WHERE {self.filter})"
        return call


@dataclass(frozen=True)
class Relationship:
    """A declared join, always pointing from the many side toward the unique one."""

    id: uuid.UUID
    name: str
    left: str
    right: str
    join_columns: tuple[tuple[str, str], ...]
    cardinality: str


@dataclass(frozen=True)
class LoadedModel:
    """One subject area, resolved and ready to compile against."""

    id: uuid.UUID
    slug: str
    name: str
    description: str | None
    status: str
    provider: str
    datasets: dict[str, Dataset] = field(default_factory=dict)
    dimensions: dict[str, Dimension] = field(default_factory=dict)
    metrics: dict[str, Metric] = field(default_factory=dict)
    relationships: tuple[Relationship, ...] = ()
    # Names that exist but were filtered out because their bindings no longer
    # resolve. Kept so a refusal can say "that is broken" rather than "there is
    # no such thing" — the two call for opposite responses, and telling somebody
    # a metric does not exist when it does is how they end up re-deriving it by
    # hand, which is exactly what this subsystem exists to prevent.
    broken_metrics: dict[str, str] = field(default_factory=dict)
    broken_dimensions: dict[str, str] = field(default_factory=dict)

    def why_missing(self, kind: str, name: str) -> str | None:
        """A better explanation than "unknown", when there is one."""
        broken = self.broken_metrics if kind == "metric" else self.broken_dimensions
        if name in broken:
            return (
                f"{kind.capitalize()} {name!r} is defined in {self.slug!r} but is "
                f"currently broken: {broken[name]} It cannot be used until it is "
                "repaired. Do not substitute your own calculation for it."
            )
        return None

    def dimensions_for(self, dataset: str) -> list[Dimension]:
        return [d for d in self.dimensions.values() if d.dataset == dataset]

    def default_time_dimension(self, dataset: str) -> Dimension | None:
        candidates = [d for d in self.dimensions_for(dataset) if d.kind == "time"]
        for dim in candidates:
            if dim.is_default_time:
                return dim
        # Exactly one time dimension is unambiguous even without the flag. Two
        # without a default is genuinely ambiguous and stays unanswered.
        return candidates[0] if len(candidates) == 1 else None


def _tuple(value) -> tuple[str, ...]:
    if not value:
        return ()
    return tuple(str(v) for v in value)


def _join_columns(value) -> tuple[tuple[str, str], ...]:
    pairs: list[tuple[str, str]] = []
    for entry in value or []:
        if isinstance(entry, dict) and entry.get("left") and entry.get("right"):
            pairs.append((str(entry["left"]), str(entry["right"])))
    return tuple(pairs)


def to_loaded(
    row: SemanticModel,
    datasets: list[SemanticDataset],
    dimensions: list[SemanticDimension],
    metrics: list[SemanticMetric],
    relationships: list[SemanticRelationship],
    catalog_slugs: dict[uuid.UUID, str],
    *,
    include_unpublished: bool,
) -> LoadedModel:
    """Assemble the in-memory model from already-fetched rows.

    ``include_unpublished`` is the trust switch. The UI passes ``True`` so an
    author can see and test what they are drafting; the assistant's path passes
    ``False``, which is what keeps a half-finished definition from answering a
    question as though it were settled. ``broken`` definitions are dropped in
    both cases — a binding that no longer resolves is not a draft, it is wrong.
    """
    by_id = {d.id: d for d in datasets}
    ds_out: dict[str, Dataset] = {}
    for d in datasets:
        if d.validation_state == "broken":
            continue
        slug = catalog_slugs.get(d.catalog_id)
        if slug is None:
            # The catalog is no longer attached to this workspace. Dropping the
            # dataset is what makes every metric on it unreachable rather than
            # compiling to a name the executor cannot resolve.
            continue
        ds_out[d.name] = Dataset(
            id=d.id,
            name=d.name,
            description=d.description,
            synonyms=_tuple(d.synonyms),
            catalog_id=d.catalog_id,
            catalog_slug=slug,
            schema_name=d.schema_name,
            table_name=d.table_name,
            primary_key=_tuple(d.primary_key),
            validation_state=d.validation_state,
        )

    dim_out: dict[str, Dimension] = {}
    dim_names: dict[uuid.UUID, str] = {}
    broken_dimensions: dict[str, str] = {}
    for dim in dimensions:
        parent = by_id.get(dim.dataset_id)
        if dim.validation_state == "broken":
            broken_dimensions[dim.name] = dim.validation_detail or "Its bindings no longer resolve."
            continue
        if parent is None or parent.name not in ds_out:
            continue
        dim_out[dim.name] = Dimension(
            id=dim.id,
            name=dim.name,
            display_name=dim.display_name,
            description=dim.description,
            synonyms=_tuple(dim.synonyms),
            kind=dim.kind,
            expr=dim.expr,
            data_type=dim.data_type,
            time_grains=_tuple(dim.time_grains) or TIME_GRAINS,
            is_default_time=bool(dim.is_default_time),
            sample_values=_tuple(dim.sample_values),
            dataset=parent.name,
            validation_state=dim.validation_state,
        )
        dim_names[dim.id] = dim.name

    metric_out: dict[str, Metric] = {}
    broken_metrics: dict[str, str] = {}
    for m in metrics:
        parent = by_id.get(m.dataset_id)
        if m.validation_state == "broken":
            broken_metrics[m.name] = m.validation_detail or "Its bindings no longer resolve."
            continue
        if parent is None or parent.name not in ds_out:
            continue
        if not include_unpublished and m.status != "published":
            continue
        metric_out[m.name] = Metric(
            id=m.id,
            name=m.name,
            display_name=m.display_name,
            description=m.description,
            synonyms=_tuple(m.synonyms),
            agg=m.agg,
            expr=m.expr,
            filter=m.filter,
            time_dimension=dim_names.get(m.time_dimension_id) if m.time_dimension_id else None,
            caveat=m.caveat,
            status=m.status,
            dataset=parent.name,
            validation_state=m.validation_state,
        )

    rel_out: list[Relationship] = []
    for r in relationships:
        left = by_id.get(r.left_dataset_id)
        right = by_id.get(r.right_dataset_id)
        if left is None or right is None:
            continue
        if left.name not in ds_out or right.name not in ds_out:
            continue
        if r.validation_state == "broken":
            continue
        rel_out.append(
            Relationship(
                id=r.id,
                name=r.name,
                left=left.name,
                right=right.name,
                join_columns=_join_columns(r.join_columns),
                cardinality=r.cardinality,
            )
        )

    return LoadedModel(
        id=row.id,
        slug=row.slug,
        name=row.name,
        description=row.description,
        status=row.status,
        provider=row.provider,
        datasets=ds_out,
        dimensions=dim_out,
        metrics=metric_out,
        relationships=tuple(rel_out),
        broken_metrics=broken_metrics,
        broken_dimensions=broken_dimensions,
    )


async def load_model(
    db: AsyncSession,
    row: SemanticModel,
    *,
    include_unpublished: bool = False,
) -> LoadedModel:
    """Fetch a model's children and assemble it.

    Four small queries rather than a join: the children are independent
    collections and the row counts are tiny by construction (a model that needs a
    join to load is a model that is too big for the assistant to use anyway).
    """
    datasets = list(
        (
            await db.execute(select(SemanticDataset).where(SemanticDataset.model_id == row.id))
        ).scalars()
    )
    dimensions = list(
        (
            await db.execute(select(SemanticDimension).where(SemanticDimension.model_id == row.id))
        ).scalars()
    )
    metrics = list(
        (
            await db.execute(select(SemanticMetric).where(SemanticMetric.model_id == row.id))
        ).scalars()
    )
    relationships = list(
        (
            await db.execute(
                select(SemanticRelationship).where(SemanticRelationship.model_id == row.id)
            )
        ).scalars()
    )

    catalog_ids = {d.catalog_id for d in datasets}
    slugs: dict[uuid.UUID, str] = {}
    if catalog_ids:
        rows = (
            await db.execute(select(Catalog.id, Catalog.slug).where(Catalog.id.in_(catalog_ids)))
        ).all()
        slugs = {cid: slug for cid, slug in rows}

    return to_loaded(
        row,
        datasets,
        dimensions,
        metrics,
        relationships,
        slugs,
        include_unpublished=include_unpublished,
    )
