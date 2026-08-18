"""DuckHaven's own semantic document: YAML in, canonical models out.

The format deliberately mirrors what the UI writes, so a definition can move
between hand-authoring and version control without changing shape, and so a
round trip through this adapter is an export format as well as an import one.

Everything here is defensive about a hand-written file. A YAML document arrives
from a person or from CI, and a typo in one metric should cost that metric, not
the whole publish — so an unusable entry is reported through ``skipped`` and the
rest of the file still lands.

    version: 1
    models:
      - slug: sales
        name: Sales
        description: Orders, revenue and the customers behind them.
        datasets:
          - name: orders
            catalog: warehouse
            schema: analytics
            table: orders
            primary_key: [id]
          - name: customers
            catalog: warehouse
            schema: analytics
            table: customers
            primary_key: [id]
            synonyms: [clients, accounts]
        relationships:
          - name: orders_to_customers
            left: orders
            right: customers
            join: [{left: customer_id, right: id}]
        dimensions:
          - name: order_date
            dataset: orders
            kind: time
            grains: [day, week, month, quarter, year]
            default_time: true
          - name: country
            dataset: customers
            synonyms: [nation, market]
            sample_values: ["United States", "Canada"]
        metrics:
          - name: revenue
            dataset: orders
            agg: sum
            expr: total_amount
            filter: "status <> 'test'"
            measured_on: order_date
            synonyms: [turnover, gmv]
            caveat: Excludes internal test orders.
"""

from __future__ import annotations

from typing import Any

import yaml

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

CARDINALITIES = ("many_to_one", "one_to_one")


class SemanticDocumentError(ValueError):
    """The document could not be read at all, as opposed to one entry being bad."""


def parse_document(payload: str | bytes | dict) -> dict:
    """Accept the document as YAML text or as already-parsed JSON."""
    if isinstance(payload, dict):
        return payload
    try:
        parsed = yaml.safe_load(payload)
    except yaml.YAMLError as exc:
        raise SemanticDocumentError(f"Could not parse the document as YAML: {exc}") from exc
    if not isinstance(parsed, dict):
        raise SemanticDocumentError("A semantic document must be a mapping with a `models` key.")
    return parsed


def _strings(value: Any, *, limit: int = 20) -> tuple[str, ...]:
    """A list of strings from whatever the document actually said.

    Hand-written YAML gets hand-written mistakes: ``synonyms: 5`` or a mapping
    where a list belongs. Slicing those raises ``TypeError``, which nothing
    upstream catches, so one typo returned 500 and imported nothing — the
    opposite of this module's contract that a mistake in one definition costs
    that definition, not the whole publish.
    """
    if not value:
        return ()
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)):
        raise SemanticDocumentError(
            f"Expected a list of values or a single string, got {type(value).__name__}."
        )
    return tuple(str(v) for v in value[:limit])


def _join_pairs(value: Any) -> tuple[tuple[str, str], ...]:
    pairs: list[tuple[str, str]] = []
    for entry in value or []:
        if isinstance(entry, dict) and entry.get("left") and entry.get("right"):
            pairs.append((str(entry["left"]), str(entry["right"])))
    return tuple(pairs)


async def models_from_yaml(payload, *, resolve: Resolver) -> ProviderModels:
    """Translate a DuckHaven semantic document into canonical models."""
    document = parse_document(payload)
    out = ProviderModels()

    raw_models = document.get("models")
    if not isinstance(raw_models, list):
        raise SemanticDocumentError("`models` must be a list.")

    for raw in raw_models:
        if not isinstance(raw, dict) or not raw.get("slug"):
            out.skipped.append(Skipped(ref="<model>", reason="missing_slug"))
            continue
        slug = str(raw["slug"])
        out.model_slugs.add(slug)

        datasets: list[CanonicalDataset] = []
        known_datasets: set[str] = set()
        for entry in raw.get("datasets") or []:
            if not isinstance(entry, dict):
                out.skipped.append(Skipped(ref=slug, reason="malformed_dataset"))
                continue
            name = entry.get("name")
            schema = entry.get("schema") or entry.get("schema_name")
            table = entry.get("table") or entry.get("table_name")
            catalog = entry.get("catalog")
            if not (name and schema and table):
                out.skipped.append(
                    Skipped(ref=f"{slug}.{name or '<dataset>'}", reason="incomplete_reference")
                )
                continue
            ref, skipped = resolve.resolve(
                catalog=catalog,
                system=None,
                schema=str(schema),
                table=str(table),
                # A semantic definition must bind to a table DuckHaven can query.
                # An external node is fine for lineage, which only describes flow;
                # here it would be a metric that can never be computed.
                allow_external=False,
            )
            if ref is None or ref.is_external:
                out.skipped.append(
                    skipped or Skipped(ref=f"{slug}.{name}", reason="unknown_catalog")
                )
                continue
            datasets.append(
                CanonicalDataset(
                    name=str(name),
                    catalog_id=ref.catalog_id,
                    schema_name=ref.schema,
                    table_name=ref.table,
                    description=entry.get("description"),
                    synonyms=_strings(entry.get("synonyms")),
                    primary_key=_strings(entry.get("primary_key")),
                )
            )
            known_datasets.add(str(name))

        dimensions: list[CanonicalDimension] = []
        known_dimensions: set[str] = set()
        for entry in raw.get("dimensions") or []:
            if not isinstance(entry, dict) or not entry.get("name"):
                out.skipped.append(Skipped(ref=slug, reason="malformed_dimension"))
                continue
            name = str(entry["name"])
            dataset = str(entry.get("dataset") or "")
            if dataset not in known_datasets:
                out.skipped.append(Skipped(ref=f"{slug}.{name}", reason="unknown_dataset"))
                continue
            kind = str(entry.get("kind") or "categorical")
            if kind not in ("categorical", "time"):
                out.skipped.append(Skipped(ref=f"{slug}.{name}", reason="unknown_dimension_kind"))
                continue
            declared = _strings(entry.get("grains") or entry.get("time_grains"))
            grains = tuple(g for g in declared if g in TIME_GRAINS)
            dimensions.append(
                CanonicalDimension(
                    name=name,
                    dataset=dataset,
                    expr=str(entry.get("expr") or name),
                    kind=kind,
                    display_name=entry.get("display_name") or entry.get("label"),
                    description=entry.get("description"),
                    synonyms=_strings(entry.get("synonyms")),
                    data_type=entry.get("data_type"),
                    time_grains=grains or (TIME_GRAINS if kind == "time" else ()),
                    is_default_time=bool(entry.get("default_time") or entry.get("is_default_time")),
                    sample_values=_strings(entry.get("sample_values")),
                )
            )
            known_dimensions.add(name)

        metrics: list[CanonicalMetric] = []
        for entry in raw.get("metrics") or []:
            if not isinstance(entry, dict) or not entry.get("name"):
                out.skipped.append(Skipped(ref=slug, reason="malformed_metric"))
                continue
            name = str(entry["name"])
            dataset = str(entry.get("dataset") or "")
            if dataset not in known_datasets:
                out.skipped.append(Skipped(ref=f"{slug}.{name}", reason="unknown_dataset"))
                continue
            agg = str(entry.get("agg") or "")
            if agg not in AGGREGATIONS:
                out.skipped.append(Skipped(ref=f"{slug}.{name}", reason="unknown_aggregation"))
                continue
            expr = entry.get("expr")
            if agg != "count" and not expr:
                out.skipped.append(Skipped(ref=f"{slug}.{name}", reason="missing_expression"))
                continue
            axis = entry.get("measured_on") or entry.get("time_dimension")
            if axis and str(axis) not in known_dimensions:
                # Reported, but the metric still lands: a metric without a time
                # axis is usable for everything except time-filtered questions,
                # and dropping it entirely would lose more than it protects.
                out.skipped.append(Skipped(ref=f"{slug}.{name}", reason="unknown_time_dimension"))
                axis = None
            metrics.append(
                CanonicalMetric(
                    name=name,
                    dataset=dataset,
                    agg=agg,
                    expr=str(expr) if expr else None,
                    filter=entry.get("filter"),
                    time_dimension=str(axis) if axis else None,
                    display_name=entry.get("display_name") or entry.get("label"),
                    description=entry.get("description"),
                    synonyms=_strings(entry.get("synonyms")),
                    caveat=entry.get("caveat"),
                )
            )

        relationships: list[CanonicalRelationship] = []
        for entry in raw.get("relationships") or []:
            if not isinstance(entry, dict) or not entry.get("name"):
                out.skipped.append(Skipped(ref=slug, reason="malformed_relationship"))
                continue
            name = str(entry["name"])
            left = str(entry.get("left") or entry.get("left_dataset") or "")
            right = str(entry.get("right") or entry.get("right_dataset") or "")
            if left not in known_datasets or right not in known_datasets:
                out.skipped.append(Skipped(ref=f"{slug}.{name}", reason="unknown_dataset"))
                continue
            pairs = _join_pairs(entry.get("join") or entry.get("join_columns"))
            if not pairs:
                out.skipped.append(Skipped(ref=f"{slug}.{name}", reason="missing_join_columns"))
                continue
            cardinality = str(entry.get("cardinality") or "many_to_one")
            if cardinality not in CARDINALITIES:
                # `one_to_many` is the interesting rejection: it is the direction
                # that multiplies fact rows and inflates every metric crossing it,
                # so it has no representation anywhere in this system.
                out.skipped.append(Skipped(ref=f"{slug}.{name}", reason="unsupported_cardinality"))
                continue
            relationships.append(
                CanonicalRelationship(
                    name=name,
                    left=left,
                    right=right,
                    join_columns=pairs,
                    cardinality=cardinality,
                )
            )

        out.models.append(
            CanonicalModel(
                slug=slug,
                name=str(raw.get("name") or slug),
                description=raw.get("description"),
                datasets=tuple(datasets),
                dimensions=tuple(dimensions),
                metrics=tuple(metrics),
                relationships=tuple(relationships),
            )
        )

    return out
