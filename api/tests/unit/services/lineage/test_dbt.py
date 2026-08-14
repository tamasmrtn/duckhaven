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
    edges, _ = edges_from_manifest(manifest, resolve=resolver)
    assert ("internal:analytics.stg_orders", "internal:analytics.dim_orders_v2") in names(edges)


def test_alias_wins_over_name(manifest, resolver):
    # dim_orders is written to dim_orders_v2; pointing lineage at the model's
    # file name would name a table that does not exist.
    edges, _ = edges_from_manifest(manifest, resolve=resolver)
    targets = {e.target.table for e in edges}
    assert "dim_orders_v2" in targets
    assert "dim_orders" not in targets


def test_sources_in_an_attached_catalog_resolve_internally(manifest, resolver):
    edges, _ = edges_from_manifest(manifest, resolve=resolver)
    assert ("internal:analytics.orders", "internal:analytics.stg_orders") in names(edges)


def test_a_source_outside_duckhaven_becomes_an_external_asset(manifest, resolver):
    # The graph keeps its roots rather than dropping an upstream it cannot own.
    edges, _ = edges_from_manifest(manifest, resolve=resolver)
    assert ("crm_pg:public.customers", "internal:analytics.stg_orders") in names(edges)


def test_seeds_and_snapshots_participate(manifest, resolver):
    edges, _ = edges_from_manifest(manifest, resolve=resolver)
    pairs = names(edges)
    assert ("internal:analytics.country_codes", "internal:analytics.dim_orders_v2") in pairs
    assert ("internal:analytics.stg_orders", "internal:analytics.orders_history") in pairs


def test_tests_are_not_lineage(manifest, resolver):
    edges, _ = edges_from_manifest(manifest, resolve=resolver)
    labels = {label for pair in names(edges) for label in pair}
    assert not any("not_null_id" in label for label in labels)


def test_disabled_resources_are_excluded(manifest, resolver):
    edges, _ = edges_from_manifest(manifest, resolve=resolver)
    assert not any(e.target.table == "disabled_model" for e in edges)


def test_a_model_targeting_an_unknown_catalog_is_skipped_and_reported(manifest, resolver):
    # A *target* DuckHaven has never heard of is a mistake, not an external asset:
    # DuckHaven cannot be building a table in a catalog it does not attach.
    edges, skipped = edges_from_manifest(manifest, resolve=resolver)
    assert not any(e.target.table == "external_target" for e in edges)
    assert any(s.reason == "unknown_catalog" for s in skipped)


def test_edges_are_marked_as_declared_model_relationships(manifest, resolver):
    edges, _ = edges_from_manifest(manifest, resolve=resolver)
    assert {e.operation for e in edges} == {"model"}
    assert {e.confidence for e in edges} == {"exact"}


def test_invocation_id_is_the_import_batch(manifest):
    assert run_id(manifest) == "b1a7c0de-0000-4000-8000-000000000001"


def test_falls_back_to_depends_on_when_parent_map_is_absent(manifest, resolver):
    manifest.pop("parent_map")
    edges, _ = edges_from_manifest(manifest, resolve=resolver)
    assert ("internal:analytics.stg_orders", "internal:analytics.dim_orders_v2") in names(edges)


def test_an_empty_manifest_yields_nothing(resolver):
    edges, skipped = edges_from_manifest({}, resolve=resolver)
    assert edges == [] and skipped == []


def test_reimporting_the_same_manifest_is_deterministic(manifest, resolver):
    first, _ = edges_from_manifest(manifest, resolve=resolver)
    second, _ = edges_from_manifest(manifest, resolve=resolver)
    assert names(first) == names(second)


def test_the_registry_resolves_the_dbt_adapter():
    assert get_adapter("dbt") is edges_from_manifest


def test_an_unknown_provider_has_no_adapter():
    with pytest.raises(KeyError):
        get_adapter("nope")
