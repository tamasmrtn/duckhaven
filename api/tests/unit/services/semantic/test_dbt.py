"""The dbt semantic adapter, against a manifest dbt actually produced.

``fixtures/dbt_manifest.json`` is a real ``dbt parse`` output — trimmed to the
keys this adapter reads, but otherwise untouched — so these tests pin the shape
dbt emits rather than the shape somebody assumed it emits.

The important cases are the refusals. dbt can express metrics DuckHaven cannot,
and the wrong response to that is a metric that looks imported but computes
something different.
"""

from __future__ import annotations

import json
import uuid
from copy import deepcopy
from pathlib import Path

import pytest

from api.services.lineage.resolve import Resolver
from api.services.semantic.providers import SemanticDocumentError
from api.services.semantic.providers.dbt import models_from_manifest

pytestmark = pytest.mark.asyncio

MANIFEST = Path(__file__).parent / "fixtures" / "dbt_manifest.json"


class FakeCatalog:
    def __init__(self, slug: str) -> None:
        self.slug = slug
        self.id = uuid.uuid4()


@pytest.fixture
def manifest() -> dict:
    return json.loads(MANIFEST.read_text())


@pytest.fixture
def resolver() -> Resolver:
    return Resolver([FakeCatalog("semcheck")])


async def only_model(manifest, resolver):
    out = await models_from_manifest(manifest, resolve=resolver)
    return out, out.models[0]


async def test_the_project_becomes_one_semantic_model(manifest, resolver):
    """A dbt project is the subject area; dbt expresses no finer grouping."""
    out, model = await only_model(manifest, resolver)

    assert model.slug == "semdemo"
    assert out.model_slugs == {"semdemo"}


async def test_semantic_models_bind_to_the_relations_dbt_built(manifest, resolver):
    _, model = await only_model(manifest, resolver)

    bound = {(d.name, d.schema_name, d.table_name) for d in model.datasets}
    assert bound == {
        ("orders", "analytics", "dbt_orders"),
        ("customers", "analytics", "dbt_customers"),
    }


async def test_the_primary_entity_becomes_the_primary_key(manifest, resolver):
    """Without it nothing can be the unique side of a join."""
    _, model = await only_model(manifest, resolver)

    keys = {d.name: d.primary_key for d in model.datasets}
    assert keys == {"orders": ("id",), "customers": ("id",)}


async def test_a_foreign_entity_becomes_a_many_to_one_join(manifest, resolver):
    _, model = await only_model(manifest, resolver)

    (rel,) = model.relationships
    assert (rel.left, rel.right) == ("orders", "customers")
    assert rel.join_columns == (("customer_id", "id"),)
    # The only direction this system can represent.
    assert rel.cardinality == "many_to_one"


async def test_the_measures_aggregation_and_expression_reach_the_metric(manifest, resolver):
    """dbt splits these across measure and metric; DuckHaven keeps them together."""
    _, model = await only_model(manifest, resolver)

    revenue = next(m for m in model.metrics if m.name == "revenue")
    assert revenue.agg == "sum"
    assert revenue.expr == "total_amount"
    assert revenue.display_name == "Revenue"


async def test_the_jinja_filter_is_translated_to_sql(manifest, resolver):
    """`{{ Dimension('order__status') }} != 'test'` has to become a column."""
    _, model = await only_model(manifest, resolver)

    revenue = next(m for m in model.metrics if m.name == "revenue")
    assert revenue.filter == "(status != 'test')"


async def test_the_time_axis_survives_the_import(manifest, resolver):
    """The field that decides whether "revenue last month" is right."""
    _, model = await only_model(manifest, resolver)

    revenue = next(m for m in model.metrics if m.name == "revenue")
    assert revenue.time_dimension == "order_date"

    order_date = next(d for d in model.dimensions if d.name == "order_date")
    assert order_date.kind == "time"
    assert order_date.is_default_time


async def test_time_granularity_becomes_the_supported_grains(manifest, resolver):
    """dbt records the finest grain; anything coarser derives from it."""
    _, model = await only_model(manifest, resolver)

    order_date = next(d for d in model.dimensions if d.name == "order_date")
    assert order_date.time_grains == ("day", "week", "month", "quarter", "year")


async def test_a_ratio_metric_is_skipped_and_reported(manifest, resolver):
    """V1 has no composition, so there is nothing honest to map it onto."""
    out, model = await only_model(manifest, resolver)

    assert all(m.name != "avg_order_value" for m in model.metrics)
    assert any("ratio" in s.reason for s in out.skipped)


async def test_an_untranslatable_filter_drops_the_metric(manifest, resolver):
    """The case where importing anyway would be actively harmful.

    A metric imported without the filter it was defined with computes over more
    rows than it should and says nothing about it — a confidently wrong number,
    which is worse than a missing one.
    """
    doctored = deepcopy(manifest)
    doctored["metrics"]["metric.semdemo.revenue"]["filter"]["where_filters"][0][
        "where_sql_template"
    ] = "{{ Dimension('order__nonexistent') }} != 'test'"

    out = await models_from_manifest(doctored, resolve=resolver)

    assert all(m.name != "revenue" for m in out.models[0].metrics)
    assert any(s.reason == "untranslatable_filter" for s in out.skipped)


async def test_an_unsupported_aggregation_drops_the_metric(manifest, resolver):
    doctored = deepcopy(manifest)
    measures = doctored["semantic_models"]["semantic_model.semdemo.orders"]["measures"]
    next(m for m in measures if m["name"] == "order_total")["agg"] = "median"

    out = await models_from_manifest(doctored, resolve=resolver)

    assert all(m.name != "revenue" for m in out.models[0].metrics)
    assert any("median" in s.reason for s in out.skipped)


async def test_a_relation_in_an_unknown_catalog_is_skipped(manifest, resolver):
    doctored = deepcopy(manifest)
    doctored["semantic_models"]["semantic_model.semdemo.orders"]["node_relation"]["database"] = (
        "somewhere_else"
    )

    out = await models_from_manifest(doctored, resolve=resolver)

    assert {d.name for d in out.models[0].datasets} == {"customers"}
    assert any(s.reason == "unknown_catalog" for s in out.skipped)


async def test_a_foreign_entity_nobody_declares_as_a_key_is_skipped(manifest, resolver):
    """No provable unique side means the join could multiply fact rows."""
    doctored = deepcopy(manifest)
    doctored["semantic_models"]["semantic_model.semdemo.customers"]["entities"] = []

    out = await models_from_manifest(doctored, resolve=resolver)

    assert out.models[0].relationships == ()
    assert any(s.reason == "no_primary_entity" for s in out.skipped)


async def test_the_manifest_is_accepted_as_raw_json_text(manifest, resolver):
    """The import route hands the artifact through as published."""
    out = await models_from_manifest(json.dumps(manifest), resolve=resolver)

    assert out.models[0].slug == "semdemo"


async def test_a_manifest_with_no_semantic_layer_yields_an_empty_model(resolver):
    out = await models_from_manifest({"metadata": {"project_name": "plain"}}, resolve=resolver)

    assert out.models[0].slug == "plain"
    assert out.models[0].metrics == ()
    # Still declared, so reconciliation does not treat it as never mentioned.
    assert out.model_slugs == {"plain"}


async def test_run_id_is_dbts_own_invocation_id(manifest):
    """Same helper name and meaning as the lineage adapter's.

    A generated id would reconcile just as correctly and tell nobody which dbt
    run produced the import; sharing dbt's invocation id lets a semantic import
    and a lineage import from one `dbt parse` be correlated afterwards.
    """
    from api.services.lineage.providers.dbt import run_id as lineage_run_id
    from api.services.semantic.providers.dbt import run_id as semantic_run_id

    assert semantic_run_id(manifest) == manifest["metadata"]["invocation_id"]
    # The two adapters agree on the batch id for the same artifact.
    assert semantic_run_id(manifest) == lineage_run_id(manifest)


async def test_run_id_is_absent_when_the_manifest_carries_none():
    from api.services.semantic.providers.dbt import run_id

    assert run_id({"metadata": {}}) is None


async def test_a_name_two_semantic_models_both_use_is_qualified(manifest, resolver):
    """dbt scopes dimension names per semantic model; DuckHaven does not.

    `status` on orders and `status` on customers is ordinary in a real project.
    Emitting both bare collides on `uq_semantic_dimensions_name` and fails the
    entire import with an integrity error — no models, no `skipped` entry, a 500.
    """
    doctored = deepcopy(manifest)
    doctored["semantic_models"]["semantic_model.semdemo.customers"]["dimensions"].append(
        {"name": "status", "type": "categorical"}
    )

    out = await models_from_manifest(doctored, resolve=resolver)

    names = [d.name for d in out.models[0].dimensions]
    assert len(names) == len(set(names))
    # Both are qualified, so which one you get never depends on manifest order.
    assert "orders_status" in names
    assert "customers_status" in names
    assert "status" not in names


async def test_an_unambiguous_name_keeps_its_bare_form(manifest, resolver):
    """Qualifying everything would make the common case worse to query."""
    _, model = await only_model(manifest, resolver)

    assert any(d.name == "order_date" for d in model.dimensions)


async def test_a_qualified_time_axis_still_binds_its_metric(manifest, resolver):
    """The rename has to follow through to `agg_time_dimension`.

    A metric left pointing at the old bare name would arrive with no time axis —
    which is exactly the unbound-metric hazard the binding exists to prevent.
    """
    doctored = deepcopy(manifest)
    doctored["semantic_models"]["semantic_model.semdemo.customers"]["dimensions"].append(
        {"name": "order_date", "type": "time", "type_params": {"time_granularity": "day"}}
    )

    out = await models_from_manifest(doctored, resolve=resolver)
    revenue = next(m for m in out.models[0].metrics if m.name == "revenue")

    assert revenue.time_dimension == "orders_order_date"
    assert any(d.name == "orders_order_date" for d in out.models[0].dimensions)


async def test_two_foreign_entities_to_one_dataset_do_not_collide(manifest, resolver):
    """A shipping and a billing address both point at the same table."""
    doctored = deepcopy(manifest)
    orders = doctored["semantic_models"]["semantic_model.semdemo.orders"]
    orders["entities"].append({"name": "customer", "type": "foreign", "expr": "billing_id"})

    out = await models_from_manifest(doctored, resolve=resolver)

    names = [r.name for r in out.models[0].relationships]
    assert len(names) == len(set(names))


# --- Artifacts that are not a dbt manifest ---------------------------------
#
# The import route reads its body as raw bytes so YAML and JSON both reach an
# adapter unmodified, which makes posting the wrong one an easy mistake. Every
# case below used to escape as an uncaught exception and return 500, blaming the
# server for the client's choice of file.


async def test_yaml_posted_to_the_dbt_provider_is_rejected_not_a_crash(resolver):
    """The live failure: a hand-written semantic YAML sent to `.../imports/dbt`."""
    document = "models:\n  - slug: sales\n    name: Sales\n"

    with pytest.raises(SemanticDocumentError, match="Could not parse the artifact as JSON"):
        await models_from_manifest(document, resolve=resolver)


async def test_the_message_points_at_the_provider_that_takes_yaml(resolver):
    """Knowing it failed is not enough; the reader needs to know where to send it."""
    with pytest.raises(SemanticDocumentError, match="duckhaven"):
        await models_from_manifest("models:\n  - slug: sales\n", resolve=resolver)


async def test_unparseable_text_is_rejected(resolver):
    with pytest.raises(SemanticDocumentError, match="Could not parse"):
        await models_from_manifest("this is not a manifest", resolve=resolver)


async def test_an_empty_body_is_rejected(resolver):
    """An empty file is a real CI outcome -- `dbt parse` failed and wrote nothing."""
    with pytest.raises(SemanticDocumentError, match="Could not parse"):
        await models_from_manifest("", resolve=resolver)


@pytest.mark.parametrize(
    ("body", "kind"),
    [("[]", "list"), ("null", "NoneType"), ('"a string"', "str"), ("42", "int")],
)
async def test_valid_json_that_is_not_an_object_is_rejected(body, kind, resolver):
    """These parse cleanly and then fail on `.get`, which is the same 500 later."""
    with pytest.raises(SemanticDocumentError, match=f"must be a JSON object, got {kind}"):
        await models_from_manifest(body, resolve=resolver)
