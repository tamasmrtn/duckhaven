"""dbt semantic models and metrics, from a ``manifest.json``.

The manifest is the artifact dbt already produces, so publishing definitions
costs a ``dbt parse`` in CI rather than a second source of truth — the same
route the lineage importer takes, for the same reason.

The two models line up more closely than they look. A dbt *semantic model* is a
dataset bound to the relation its ``model`` built; its *primary entity* is that
dataset's key; its *foreign entities* are joins toward whichever dataset declares
the matching primary entity. A dbt *simple metric* names a measure, and the
measure carries the aggregation, the expression and the time axis — which is
exactly a DuckHaven metric with the pieces distributed differently.

Where they do not line up, this skips and reports rather than approximating.
Two cases are worth calling out because approximating them would be actively
harmful:

*A filter that cannot be translated skips the whole metric.* dbt writes filters
as Jinja (``{{ Dimension('order__status') }} != 'test'``). If any part of one
cannot be resolved to a column, importing the metric anyway would produce a
definition that silently computes over more rows than it should — a confidently
wrong number, which is the one outcome this subsystem exists to prevent. Losing
the metric is recoverable; importing a wrong one is not noticed.

*Ratio, derived, cumulative and conversion metrics are skipped.* V1 has no
composition, so there is nothing honest to map them onto.
"""

from __future__ import annotations

import json
import re
from typing import Any

from api.services.lineage.resolve import Resolver, Skipped
from api.services.semantic.ingest import (
    CanonicalDataset,
    CanonicalDimension,
    CanonicalMetric,
    CanonicalModel,
    CanonicalRelationship,
)
from api.services.semantic.model import AGGREGATIONS, TIME_GRAINS
from api.services.semantic.providers import ProviderModels

# dbt's aggregation vocabulary onto DuckHaven's. The ones with no entry —
# median, percentile, sum_boolean — have no V1 equivalent and skip the metric.
_AGGREGATIONS = {
    "sum": "sum",
    "count": "count",
    "count_distinct": "count_distinct",
    "average": "avg",
    "avg": "avg",
    "min": "min",
    "max": "max",
}

# Only `simple` maps cleanly. The rest compose out of other metrics, which V1
# cannot express.
_SUPPORTED_METRIC_TYPES = frozenset({"simple"})

# `{{ Dimension('order__status') }}` / `{{ TimeDimension('order__created','day') }}`
_JINJA_REF = re.compile(
    r"\{\{\s*(Dimension|TimeDimension|Entity)\s*\(\s*'([^']+)'"
    r"(?:\s*,\s*'[^']*')?\s*\)\s*\}\}"
)
_ANY_JINJA = re.compile(r"\{\{.*?\}\}", re.DOTALL)


def _grains_from(granularity: str | None) -> tuple[str, ...]:
    """Every grain at or coarser than dbt's smallest supported one.

    dbt records the *finest* granularity a time dimension supports; anything
    coarser can be derived from it, so the supported set runs from there upward.
    """
    if not granularity:
        return TIME_GRAINS
    granularity = granularity.lower()
    if granularity not in TIME_GRAINS:
        return TIME_GRAINS
    return TIME_GRAINS[TIME_GRAINS.index(granularity) :]


def _entities(semantic_model: dict, kind: str) -> list[dict]:
    return [e for e in semantic_model.get("entities") or [] if e.get("type") == kind]


def _column_of(entry: dict) -> str:
    """A dbt entity/dimension's column: its ``expr``, or its name when unset."""
    return str(entry.get("expr") or entry.get("name"))


def _translate_filter(template: str, dimensions_by_name: dict[str, str]) -> str | None:
    """Turn one dbt Jinja filter into plain SQL, or None if anything is unresolved.

    Returning None is a refusal, not a fallback: the caller drops the metric
    rather than importing it without the filter it was defined with.
    """
    unresolved: list[str] = []

    def replace(match: re.Match) -> str:
        kind, ref = match.group(1), match.group(2)
        # dbt qualifies as `entity__dimension`; the dimension is the last segment.
        name = ref.split("__")[-1]
        if kind == "Entity":
            unresolved.append(ref)
            return match.group(0)
        column = dimensions_by_name.get(name)
        if column is None:
            unresolved.append(ref)
            return match.group(0)
        return column

    rendered = _JINJA_REF.sub(replace, template)
    if unresolved or _ANY_JINJA.search(rendered):
        return None
    return rendered.strip()


def _filter_templates(metric: dict) -> list[str]:
    node = metric.get("filter") or {}
    return [
        str(f.get("where_sql_template"))
        for f in node.get("where_filters") or []
        if f.get("where_sql_template")
    ]


async def models_from_manifest(payload, *, resolve: Resolver) -> ProviderModels:
    """Translate a dbt manifest's semantic models and metrics into one model.

    A dbt project maps to a single DuckHaven semantic model — a project *is* the
    subject area, and splitting it would need a grouping dbt does not express.
    """
    manifest = payload if isinstance(payload, dict) else json.loads(payload)
    out = ProviderModels()

    semantic_models: dict[str, Any] = manifest.get("semantic_models") or {}
    metrics: dict[str, Any] = manifest.get("metrics") or {}
    project = (
        (manifest.get("metadata") or {}).get("project_name")
        or next(iter(semantic_models.values()), {}).get("package_name")
        or "dbt"
    )
    slug = re.sub(r"[^a-z0-9_]+", "_", str(project).lower()).strip("_") or "dbt"
    out.model_slugs.add(slug)

    datasets: list[CanonicalDataset] = []
    dimensions: list[CanonicalDimension] = []
    relationships: list[CanonicalRelationship] = []
    canonical_metrics: list[CanonicalMetric] = []

    # unique_id -> dataset name, so a metric can find the dataset it belongs to.
    dataset_of: dict[str, str] = {}
    # dataset -> {dimension name: column expression}, for translating filters.
    columns_of: dict[str, dict[str, str]] = {}
    # dataset -> its default time axis.
    default_axis: dict[str, str | None] = {}
    # entity name -> (dataset, key column), from primary entities only.
    primary_entities: dict[str, tuple[str, str]] = {}
    # measure name -> (dataset, agg, expr, agg_time_dimension)
    measures: dict[str, tuple[str, str, str | None, str | None]] = {}

    for unique_id, sm in semantic_models.items():
        name = str(sm.get("name") or "")
        relation = sm.get("node_relation") or {}
        schema = relation.get("schema_name")
        table = relation.get("alias")
        catalog = relation.get("database")
        if not (name and schema and table):
            out.skipped.append(Skipped(ref=unique_id, reason="incomplete_reference"))
            continue

        ref, skipped = resolve.resolve(
            catalog=catalog,
            system=None,
            schema=str(schema),
            table=str(table),
            # A semantic definition has to bind to something DuckHaven can query.
            allow_external=False,
        )
        if ref is None or ref.is_external:
            out.skipped.append(skipped or Skipped(ref=unique_id, reason="unknown_catalog"))
            continue

        primary = _entities(sm, "primary")
        key = tuple(_column_of(e) for e in primary[:1])
        datasets.append(
            CanonicalDataset(
                name=name,
                catalog_id=ref.catalog_id,
                schema_name=ref.schema,
                table_name=ref.table,
                description=sm.get("description"),
                primary_key=key,
            )
        )
        dataset_of[unique_id] = name
        columns_of[name] = {}
        default_axis[name] = (sm.get("defaults") or {}).get("agg_time_dimension")
        for entity in primary:
            primary_entities[str(entity.get("name"))] = (name, _column_of(entity))

        for dim in sm.get("dimensions") or []:
            dim_name = str(dim.get("name") or "")
            if not dim_name:
                continue
            kind = "time" if dim.get("type") == "time" else "categorical"
            column = _column_of(dim)
            columns_of[name][dim_name] = column
            grains = ()
            if kind == "time":
                grains = _grains_from((dim.get("type_params") or {}).get("time_granularity"))
            dimensions.append(
                CanonicalDimension(
                    name=dim_name,
                    dataset=name,
                    expr=column,
                    kind=kind,
                    description=dim.get("description"),
                    display_name=dim.get("label"),
                    time_grains=grains,
                    is_default_time=kind == "time" and dim_name == default_axis[name],
                )
            )

        for measure in sm.get("measures") or []:
            measures[str(measure.get("name"))] = (
                name,
                str(measure.get("agg") or ""),
                measure.get("expr"),
                measure.get("agg_time_dimension") or default_axis[name],
            )

    # Foreign entities become joins toward whoever declares the primary one.
    for unique_id, sm in semantic_models.items():
        left = dataset_of.get(unique_id)
        if left is None:
            continue
        for entity in _entities(sm, "foreign") + _entities(sm, "unique"):
            entity_name = str(entity.get("name"))
            target = primary_entities.get(entity_name)
            if target is None or target[0] == left:
                # Nothing declares it as a key, so there is no provable unique
                # side and the join would risk multiplying rows.
                out.skipped.append(Skipped(ref=f"{left}.{entity_name}", reason="no_primary_entity"))
                continue
            right, right_column = target
            relationships.append(
                CanonicalRelationship(
                    name=f"{left}_to_{right}",
                    left=left,
                    right=right,
                    join_columns=((_column_of(entity), right_column),),
                    cardinality="many_to_one",
                )
            )

    for unique_id, metric in metrics.items():
        name = str(metric.get("name") or "")
        kind = str(metric.get("type") or "")
        if kind not in _SUPPORTED_METRIC_TYPES:
            out.skipped.append(Skipped(ref=unique_id, reason=f"unsupported_metric_type_{kind}"))
            continue

        measure_ref = (metric.get("type_params") or {}).get("measure") or {}
        measure_name = measure_ref.get("name")
        found = measures.get(str(measure_name))
        if found is None:
            out.skipped.append(Skipped(ref=unique_id, reason="unknown_measure"))
            continue
        dataset, dbt_agg, expr, axis = found

        agg = _AGGREGATIONS.get(dbt_agg.lower())
        if agg is None:
            out.skipped.append(Skipped(ref=unique_id, reason=f"unsupported_aggregation_{dbt_agg}"))
            continue
        if agg not in AGGREGATIONS:
            out.skipped.append(Skipped(ref=unique_id, reason="unsupported_aggregation"))
            continue
        if agg != "count" and not expr:
            out.skipped.append(Skipped(ref=unique_id, reason="missing_expression"))
            continue

        templates = _filter_templates(metric)
        translated: list[str] = []
        for template in templates:
            rendered = _translate_filter(template, columns_of.get(dataset, {}))
            if rendered is None:
                translated = []
                break
            translated.append(f"({rendered})")
        if templates and not translated:
            # Importing it without the filter would quietly widen what it counts.
            out.skipped.append(Skipped(ref=unique_id, reason="untranslatable_filter"))
            continue

        canonical_metrics.append(
            CanonicalMetric(
                name=name,
                dataset=dataset,
                agg=agg,
                expr=str(expr) if expr else None,
                filter=" AND ".join(translated) or None,
                time_dimension=str(axis) if axis else None,
                display_name=metric.get("label"),
                description=metric.get("description"),
            )
        )

    out.models.append(
        CanonicalModel(
            slug=slug,
            name=str(project),
            description=f"Imported from the dbt project {project!r}.",
            datasets=tuple(datasets),
            dimensions=tuple(dimensions),
            metrics=tuple(canonical_metrics),
            relationships=tuple(relationships),
        )
    )
    return out
