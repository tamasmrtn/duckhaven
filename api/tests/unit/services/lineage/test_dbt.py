"""Mapping a dbt manifest onto the generic lineage model."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

from api.models.catalog import Catalog
from api.services.lineage.providers import get_adapter
from api.services.lineage.providers.dbt import edges_from_manifest, run_id
from api.services.lineage.resolve import Resolver

MANIFEST = Path(__file__).parent / "fixtures" / "manifest.json"


async def _unpack(manifest, *, resolve, catalog=None):
    """Adapters return a ProviderEdges; most tests only care about three fields."""
    produced = await edges_from_manifest(manifest, resolve=resolve, catalog=catalog)
    return produced.edges, produced.skipped, produced.targets


@pytest.fixture
def manifest() -> dict:
    return json.loads(MANIFEST.read_text())


@pytest.fixture
def resolver() -> Resolver:
    """A workspace attaching `warehouse` and `raw` — but not `crm_pg`."""
    return Resolver(
        [
            Catalog(id=uuid.uuid4(), slug="warehouse", name="warehouse", polaris_name="w"),
            Catalog(id=uuid.uuid4(), slug="raw", name="raw", polaris_name="r"),
        ]
    )


def names(edges) -> set[tuple[str, str]]:
    def label(ref):
        head = ref.system if ref.is_external else "internal"
        return f"{head}:{ref.schema}.{ref.table}"

    return {(label(e.source), label(e.target)) for e in edges}


async def test_model_dependencies_become_edges(manifest, resolver):
    edges, _, _t = await _unpack(manifest, resolve=resolver)
    assert ("internal:analytics.stg_orders", "internal:analytics.dim_orders_v2") in names(edges)


async def test_alias_wins_over_name(manifest, resolver):
    # dim_orders is written to dim_orders_v2; pointing lineage at the model's
    # file name would name a table that does not exist.
    edges, _, _t = await _unpack(manifest, resolve=resolver)
    targets = {e.target.table for e in edges}
    assert "dim_orders_v2" in targets
    assert "dim_orders" not in targets


async def test_sources_resolve_by_identifier_not_by_name(manifest, resolver):
    """A dbt source has no `alias`; its physical table is `identifier`, and
    `name` is only the handle `source()` uses. Reading `name` names a table that
    does not exist, so the imported graph never joins up with the real one."""
    edges, _, _t = await _unpack(manifest, resolve=resolver)
    pairs = names(edges)
    assert ("internal:analytics.orders_raw_v2", "internal:analytics.stg_orders") in pairs
    assert ("internal:analytics.orders", "internal:analytics.stg_orders") not in pairs


async def test_a_source_outside_duckhaven_becomes_an_external_asset(manifest, resolver):
    # The graph keeps its roots rather than dropping an upstream it cannot own,
    # and still uses the identifier for the table name.
    edges, _, _t = await _unpack(manifest, resolve=resolver)
    assert ("crm_pg:public.customers_export", "internal:analytics.stg_orders") in names(edges)


async def test_seeds_and_snapshots_participate(manifest, resolver):
    edges, _, _t = await _unpack(manifest, resolve=resolver)
    pairs = names(edges)
    assert ("internal:analytics.country_codes", "internal:analytics.dim_orders_v2") in pairs
    assert ("internal:analytics.stg_orders", "internal:analytics.orders_history") in pairs


async def test_tests_are_not_lineage(manifest, resolver):
    edges, _, _t = await _unpack(manifest, resolve=resolver)
    labels = {label for pair in names(edges) for label in pair}
    assert not any("not_null_id" in label for label in labels)


async def test_disabled_resources_are_excluded(manifest, resolver):
    edges, _, _t = await _unpack(manifest, resolve=resolver)
    assert not any(e.target.table == "disabled_model" for e in edges)


async def test_a_model_targeting_an_unknown_catalog_is_skipped_and_reported(manifest, resolver):
    # A *target* DuckHaven has never heard of is a mistake, not an external asset:
    # DuckHaven cannot be building a table in a catalog it does not attach.
    edges, skipped, _t = await _unpack(manifest, resolve=resolver)
    assert not any(e.target.table == "external_target" for e in edges)
    assert any(s.reason == "unknown_catalog" for s in skipped)


async def test_edges_are_marked_as_declared_model_relationships(manifest, resolver):
    edges, _, _t = await _unpack(manifest, resolve=resolver)
    assert {e.operation for e in edges} == {"model"}
    assert {e.confidence for e in edges} == {"exact"}


def test_invocation_id_is_the_import_batch(manifest):
    assert run_id(manifest) == "b1a7c0de-0000-4000-8000-000000000001"


async def test_falls_back_to_depends_on_when_parent_map_is_absent(manifest, resolver):
    manifest.pop("parent_map")
    edges, _, _t = await _unpack(manifest, resolve=resolver)
    assert ("internal:analytics.stg_orders", "internal:analytics.dim_orders_v2") in names(edges)


async def test_an_empty_manifest_yields_nothing(resolver):
    edges, skipped, _t = await _unpack({}, resolve=resolver)
    assert edges == [] and skipped == []


async def test_reimporting_the_same_manifest_is_deterministic(manifest, resolver):
    first, _, _t = await _unpack(manifest, resolve=resolver)
    second, _, _t2 = await _unpack(manifest, resolve=resolver)
    assert names(first) == names(second)


def test_the_registry_resolves_the_dbt_adapter():
    assert get_adapter("dbt") is edges_from_manifest


def test_an_unknown_provider_has_no_adapter():
    with pytest.raises(KeyError):
        get_adapter("nope")


async def test_a_model_that_lost_every_dependency_is_still_a_reconcile_target(manifest, resolver):
    """Otherwise its stale edges can never be pruned.

    Rewrite a model so it refs nothing and it contributes no edge — so scoping
    reconciliation to the *edges'* targets would leave whatever it used to
    declare asserted forever, with only an owner-level purge to clear it.
    """
    manifest["parent_map"]["model.acme.dim_orders"] = []
    manifest["nodes"]["model.acme.dim_orders"]["depends_on"]["nodes"] = []

    edges, _, targets = await _unpack(manifest, resolve=resolver)

    assert not any(e.target.table == "dim_orders_v2" for e in edges)
    assert any(key.endswith("/analytics/dim_orders_v2") for key in targets)


async def test_sources_are_never_reconcile_targets(manifest, resolver):
    # dbt does not build a source, so pruning "edges into it" is meaningless.
    _edges, _skipped, targets = await _unpack(manifest, resolve=resolver)
    assert not any(key.endswith("/analytics/orders_raw_v2") for key in targets)


async def test_an_ephemeral_model_is_spliced_through(manifest, resolver):
    """dbt inlines an ephemeral model into its consumer rather than building a
    table, so naming it would invent a relation *and* lose the real one."""
    edges, _, targets = await _unpack(manifest, resolve=resolver)
    pairs = names(edges)

    assert ("internal:analytics.stg_orders", "internal:analytics.fct_orders") in pairs
    assert not any("int_orders" in label for pair in pairs for label in pair)
    assert not any(key.endswith("/analytics/int_orders") for key in targets)


# --- column-level detail ------------------------------------------------------
#
# dbt publishes no column-to-column derivation of its own, so all of this comes
# from re-reading each model's `compiled_code` — the SQL dbt actually ran, with
# every ref() already resolved — against the column lists in `catalog.json`.


@pytest.fixture
def catalog() -> dict:
    return json.loads((Path(__file__).parent / "fixtures" / "catalog.json").read_text())


def _pairs(edges, source_table, target_table):
    edge = next(
        e for e in edges if e.source.table == source_table and e.target.table == target_table
    )
    return edge, {(c.source_column, c.target_column) for c in edge.columns}


async def test_without_a_catalog_the_import_stays_table_level(manifest, resolver):
    """`catalog.json` is what carries the source schemas; there is no guessing without it."""
    edges, _, _t = await _unpack(manifest, resolve=resolver)

    assert edges  # table-level lineage is unaffected
    assert all(e.column_lineage == "unknown" for e in edges)
    assert all(e.columns == () for e in edges)


async def test_compiled_sql_yields_column_lineage(manifest, resolver, catalog):
    edges, _, _t = await _unpack(manifest, resolve=resolver, catalog=catalog)

    edge, pairs = _pairs(edges, "orders_raw_v2", "stg_orders")
    assert edge.column_lineage == "derived"
    assert pairs == {
        ("id", "order_id"),
        ("amount", "total"),
        ("quantity", "total"),
    }


async def test_many_upstream_columns_feed_one_target_column(manifest, resolver, catalog):
    edges, _, _t = await _unpack(manifest, resolve=resolver, catalog=catalog)
    _edge, pairs = _pairs(edges, "orders_raw_v2", "stg_orders")

    assert {s for s, t in pairs if t == "total"} == {"amount", "quantity"}


async def test_one_upstream_column_feeds_many_target_columns(manifest, resolver, catalog):
    edges, _, _t = await _unpack(manifest, resolve=resolver, catalog=catalog)
    _edge, pairs = _pairs(edges, "stg_orders", "dim_orders_v2")

    assert {t for s, t in pairs if s == "total"} == {"order_total", "order_total_copy"}


async def test_a_filter_only_dependency_is_reported_as_carrying_no_columns(
    manifest, resolver, catalog
):
    """The thing table-level lineage could never say.

    dim_orders joins the country_codes seed and filters on it, but takes none of
    its values. `derived` with no columns states that; it is not the same as
    having failed to work it out.
    """
    edges, _, _t = await _unpack(manifest, resolve=resolver, catalog=catalog)

    edge, pairs = _pairs(edges, "country_codes", "dim_orders_v2")
    assert edge.column_lineage == "derived"
    assert pairs == set()


async def test_a_column_from_an_unresolvable_source_is_not_reported_as_no_flow(
    manifest, resolver, catalog
):
    """crm_pg is outside DuckHaven, so its columns cannot be tied to an asset.

    Data really does flow from it — `c.full_name` becomes `customer_name` — so
    calling this `derived` with no columns would state the opposite of the truth.
    Not being able to look is `unsupported`, and the table edge stands regardless.
    """
    edges, _, _t = await _unpack(manifest, resolve=resolver, catalog=catalog)

    edge = next(e for e in edges if e.source.is_external and e.target.table == "stg_orders")
    assert edge.source.system == "crm_pg"
    assert edge.column_lineage == "unsupported"
    assert edge.columns == ()


async def test_a_model_that_was_never_compiled_keeps_its_table_edge(manifest, resolver, catalog):
    """`compiled_code` only exists once dbt has compiled; a parse alone has none."""
    edges, _, _t = await _unpack(manifest, resolve=resolver, catalog=catalog)

    edge, pairs = _pairs(edges, "stg_orders", "fct_orders")
    assert edge.column_lineage == "unknown"
    assert pairs == set()


async def test_a_source_missing_from_the_catalog_declines_rather_than_guessing(
    manifest, resolver, catalog
):
    """Star expansion against a half-known schema would invent relationships."""
    manifest["nodes"]["model.acme.stg_orders"]["compiled_code"] = (
        "select * from raw.analytics.orders_raw_v2"
    )
    del catalog["sources"]["source.acme.raw.orders"]

    edges, _, _t = await _unpack(manifest, resolve=resolver, catalog=catalog)

    edge, pairs = _pairs(edges, "orders_raw_v2", "stg_orders")
    assert edge.column_lineage == "unsupported"
    assert pairs == set()


async def test_star_expands_against_the_catalog(manifest, resolver, catalog):
    manifest["nodes"]["model.acme.stg_orders"]["compiled_code"] = (
        "select * from raw.analytics.orders_raw_v2"
    )

    edges, _, _t = await _unpack(manifest, resolve=resolver, catalog=catalog)

    _edge, pairs = _pairs(edges, "orders_raw_v2", "stg_orders")
    assert pairs == {
        ("id", "id"),
        ("customer_id", "customer_id"),
        ("amount", "amount"),
        ("quantity", "quantity"),
        ("status", "status"),
    }


async def test_column_detail_does_not_disturb_the_table_graph(manifest, resolver, catalog):
    """The edges themselves must be identical with and without the second artifact."""
    plain, _, plain_targets = await _unpack(manifest, resolve=resolver)
    detailed, _, detailed_targets = await _unpack(manifest, resolve=resolver, catalog=catalog)

    assert names(plain) == names(detailed)
    assert plain_targets == detailed_targets
