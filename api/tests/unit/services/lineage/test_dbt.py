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


def _unpack(manifest, *, resolve):
    """Adapters return a ProviderEdges; most tests only care about two fields."""
    produced = edges_from_manifest(manifest, resolve=resolve)
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


def test_model_dependencies_become_edges(manifest, resolver):
    edges, _, _t = _unpack(manifest, resolve=resolver)
    assert ("internal:analytics.stg_orders", "internal:analytics.dim_orders_v2") in names(edges)


def test_alias_wins_over_name(manifest, resolver):
    # dim_orders is written to dim_orders_v2; pointing lineage at the model's
    # file name would name a table that does not exist.
    edges, _, _t = _unpack(manifest, resolve=resolver)
    targets = {e.target.table for e in edges}
    assert "dim_orders_v2" in targets
    assert "dim_orders" not in targets


def test_sources_resolve_by_identifier_not_by_name(manifest, resolver):
    """A dbt source has no `alias`; its physical table is `identifier`, and
    `name` is only the handle `source()` uses. Reading `name` names a table that
    does not exist, so the imported graph never joins up with the real one."""
    edges, _, _t = _unpack(manifest, resolve=resolver)
    pairs = names(edges)
    assert ("internal:analytics.orders_raw_v2", "internal:analytics.stg_orders") in pairs
    assert ("internal:analytics.orders", "internal:analytics.stg_orders") not in pairs


def test_a_source_outside_duckhaven_becomes_an_external_asset(manifest, resolver):
    # The graph keeps its roots rather than dropping an upstream it cannot own,
    # and still uses the identifier for the table name.
    edges, _, _t = _unpack(manifest, resolve=resolver)
    assert ("crm_pg:public.customers_export", "internal:analytics.stg_orders") in names(edges)


def test_seeds_and_snapshots_participate(manifest, resolver):
    edges, _, _t = _unpack(manifest, resolve=resolver)
    pairs = names(edges)
    assert ("internal:analytics.country_codes", "internal:analytics.dim_orders_v2") in pairs
    assert ("internal:analytics.stg_orders", "internal:analytics.orders_history") in pairs


def test_tests_are_not_lineage(manifest, resolver):
    edges, _, _t = _unpack(manifest, resolve=resolver)
    labels = {label for pair in names(edges) for label in pair}
    assert not any("not_null_id" in label for label in labels)


def test_disabled_resources_are_excluded(manifest, resolver):
    edges, _, _t = _unpack(manifest, resolve=resolver)
    assert not any(e.target.table == "disabled_model" for e in edges)


def test_a_model_targeting_an_unknown_catalog_is_skipped_and_reported(manifest, resolver):
    # A *target* DuckHaven has never heard of is a mistake, not an external asset:
    # DuckHaven cannot be building a table in a catalog it does not attach.
    edges, skipped, _t = _unpack(manifest, resolve=resolver)
    assert not any(e.target.table == "external_target" for e in edges)
    assert any(s.reason == "unknown_catalog" for s in skipped)


def test_edges_are_marked_as_declared_model_relationships(manifest, resolver):
    edges, _, _t = _unpack(manifest, resolve=resolver)
    assert {e.operation for e in edges} == {"model"}
    assert {e.confidence for e in edges} == {"exact"}


def test_invocation_id_is_the_import_batch(manifest):
    assert run_id(manifest) == "b1a7c0de-0000-4000-8000-000000000001"


def test_falls_back_to_depends_on_when_parent_map_is_absent(manifest, resolver):
    manifest.pop("parent_map")
    edges, _, _t = _unpack(manifest, resolve=resolver)
    assert ("internal:analytics.stg_orders", "internal:analytics.dim_orders_v2") in names(edges)


def test_an_empty_manifest_yields_nothing(resolver):
    edges, skipped, _t = _unpack({}, resolve=resolver)
    assert edges == [] and skipped == []


def test_reimporting_the_same_manifest_is_deterministic(manifest, resolver):
    first, _, _t = _unpack(manifest, resolve=resolver)
    second, _, _t2 = _unpack(manifest, resolve=resolver)
    assert names(first) == names(second)


def test_the_registry_resolves_the_dbt_adapter():
    assert get_adapter("dbt") is edges_from_manifest


def test_an_unknown_provider_has_no_adapter():
    with pytest.raises(KeyError):
        get_adapter("nope")


def test_a_model_that_lost_every_dependency_is_still_a_reconcile_target(manifest, resolver):
    """Otherwise its stale edges can never be pruned.

    Rewrite a model so it refs nothing and it contributes no edge — so scoping
    reconciliation to the *edges'* targets would leave whatever it used to
    declare asserted forever, with only an owner-level purge to clear it.
    """
    manifest["parent_map"]["model.acme.dim_orders"] = []
    manifest["nodes"]["model.acme.dim_orders"]["depends_on"]["nodes"] = []

    edges, _, targets = _unpack(manifest, resolve=resolver)

    assert not any(e.target.table == "dim_orders_v2" for e in edges)
    assert any(key.endswith("/analytics/dim_orders_v2") for key in targets)


def test_sources_are_never_reconcile_targets(manifest, resolver):
    # dbt does not build a source, so pruning "edges into it" is meaningless.
    _edges, _skipped, targets = _unpack(manifest, resolve=resolver)
    assert not any(key.endswith("/analytics/orders_raw_v2") for key in targets)


def test_an_ephemeral_model_is_spliced_through(manifest, resolver):
    """dbt inlines an ephemeral model into its consumer rather than building a
    table, so naming it would invent a relation *and* lose the real one."""
    edges, _, targets = _unpack(manifest, resolve=resolver)
    pairs = names(edges)

    assert ("internal:analytics.stg_orders", "internal:analytics.fct_orders") in pairs
    assert not any("int_orders" in label for pair in pairs for label in pair)
    assert not any(key.endswith("/analytics/int_orders") for key in targets)
