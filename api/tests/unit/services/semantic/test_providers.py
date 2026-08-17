"""The native YAML adapter, and what it refuses to import.

The behaviour worth pinning is partial success. A semantic document is written by
a person or generated in CI, and a typo in one metric must cost that metric — not
the whole publish. Every rejection is reported through ``skipped`` rather than
dropped, because a definition that silently failed to import is indistinguishable
from one nobody ever wrote.
"""

from __future__ import annotations

import uuid

import pytest

from api.services.lineage.resolve import Resolver
from api.services.semantic.providers.native import (
    SemanticDocumentError,
    models_from_yaml,
)

pytestmark = pytest.mark.asyncio


class FakeCatalog:
    def __init__(self, slug: str) -> None:
        self.slug = slug
        self.id = uuid.uuid4()


@pytest.fixture
def resolver() -> Resolver:
    return Resolver([FakeCatalog("warehouse")])


GOOD = """
version: 1
models:
  - slug: sales
    name: Sales
    description: Orders and the customers behind them.
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
        synonyms: [clients]
    relationships:
      - name: orders_to_customers
        left: orders
        right: customers
        join: [{left: customer_id, right: id}]
    dimensions:
      - name: order_date
        dataset: orders
        kind: time
        grains: [day, month]
        default_time: true
      - name: country
        dataset: customers
        synonyms: [nation]
        sample_values: ["United States"]
    metrics:
      - name: revenue
        dataset: orders
        agg: sum
        expr: total_amount
        filter: "status <> 'test'"
        measured_on: order_date
        synonyms: [turnover]
        caveat: Excludes internal test orders.
"""


async def test_a_complete_document_imports(resolver):
    out = await models_from_yaml(GOOD, resolve=resolver)

    assert out.model_slugs == {"sales"}
    model = out.models[0]
    assert model.name == "Sales"
    assert {d.name for d in model.datasets} == {"orders", "customers"}
    assert {d.name for d in model.dimensions} == {"order_date", "country"}
    assert model.metrics[0].name == "revenue"
    assert model.metrics[0].time_dimension == "order_date"
    assert model.relationships[0].cardinality == "many_to_one"
    assert out.skipped == []


async def test_the_time_axis_survives_the_round_trip(resolver):
    """The field most worth carrying across an import."""
    out = await models_from_yaml(GOOD, resolve=resolver)

    assert out.models[0].metrics[0].time_dimension == "order_date"
    date_dim = next(d for d in out.models[0].dimensions if d.name == "order_date")
    assert date_dim.kind == "time"
    assert date_dim.is_default_time
    assert date_dim.time_grains == ("day", "month")


async def test_a_dataset_in_an_unknown_catalog_is_skipped(resolver):
    document = GOOD.replace(
        "catalog: warehouse\n        schema: analytics\n        table: orders",
        "catalog: nowhere\n        schema: analytics\n        table: orders",
        1,
    )

    out = await models_from_yaml(document, resolve=resolver)

    assert any(s.reason == "unknown_catalog" for s in out.skipped)
    assert {d.name for d in out.models[0].datasets} == {"customers"}


async def test_a_metric_on_a_missing_dataset_is_skipped_not_fatal(resolver):
    document = (
        GOOD
        + """
      - name: orphan
        dataset: nowhere
        agg: sum
        expr: x
"""
    )

    out = await models_from_yaml(document, resolve=resolver)

    assert {m.name for m in out.models[0].metrics} == {"revenue"}
    assert any(s.reason == "unknown_dataset" for s in out.skipped)


async def test_a_sum_without_an_expression_is_skipped(resolver):
    document = (
        GOOD
        + """
      - name: broken
        dataset: orders
        agg: sum
"""
    )

    out = await models_from_yaml(document, resolve=resolver)

    assert any(s.reason == "missing_expression" for s in out.skipped)


async def test_an_unknown_aggregation_is_skipped(resolver):
    document = (
        GOOD
        + """
      - name: weird
        dataset: orders
        agg: median
        expr: total_amount
"""
    )

    out = await models_from_yaml(document, resolve=resolver)

    assert any(s.reason == "unknown_aggregation" for s in out.skipped)


async def test_a_fan_out_relationship_is_refused(resolver):
    """``one_to_many`` has no representation anywhere in this system."""
    document = GOOD.replace(
        "        join: [{left: customer_id, right: id}]",
        "        join: [{left: customer_id, right: id}]\n        cardinality: one_to_many",
    )

    out = await models_from_yaml(document, resolve=resolver)

    assert out.models[0].relationships == ()
    assert any(s.reason == "unsupported_cardinality" for s in out.skipped)


async def test_a_metric_naming_a_dimension_that_is_not_there_still_imports(resolver):
    """Losing the axis is worth reporting; losing the metric is not worth doing.

    A metric without a time binding still answers every question that has no time
    filter, so dropping it entirely would cost more than it protects.
    """
    document = GOOD.replace("measured_on: order_date", "measured_on: nonexistent_date")

    out = await models_from_yaml(document, resolve=resolver)

    metric = out.models[0].metrics[0]
    assert metric.name == "revenue"
    assert metric.time_dimension is None
    assert any(s.reason == "unknown_time_dimension" for s in out.skipped)


async def test_a_model_without_a_slug_is_skipped(resolver):
    out = await models_from_yaml("models:\n  - name: No Slug\n", resolve=resolver)

    assert out.models == []
    assert out.skipped[0].reason == "missing_slug"


async def test_a_declared_but_empty_model_still_counts_as_declared(resolver):
    """So reconciliation does not retire a model that merely lost its last metric."""
    out = await models_from_yaml("models:\n  - slug: empty\n    name: Empty\n", resolve=resolver)

    assert out.model_slugs == {"empty"}


async def test_unparseable_yaml_fails_loudly(resolver):
    with pytest.raises(SemanticDocumentError, match="Could not parse"):
        await models_from_yaml("models: [\n  unbalanced", resolve=resolver)


async def test_a_document_that_is_not_a_mapping_fails(resolver):
    with pytest.raises(SemanticDocumentError, match="mapping"):
        await models_from_yaml("- just\n- a\n- list\n", resolve=resolver)


async def test_models_must_be_a_list(resolver):
    with pytest.raises(SemanticDocumentError, match="`models` must be a list"):
        await models_from_yaml("models: sales\n", resolve=resolver)


async def test_json_is_accepted_as_well_as_yaml(resolver):
    """A dict arrives already parsed when a client posts JSON."""
    out = await models_from_yaml({"models": [{"slug": "sales", "name": "Sales"}]}, resolve=resolver)

    assert out.models[0].slug == "sales"
