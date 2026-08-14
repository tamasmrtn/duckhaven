"""The canonical asset key: shape, stability, and what it deliberately ignores."""

from __future__ import annotations

import uuid

import pytest

from api.services.lineage.keys import (
    asset_key,
    external_ref,
    internal_ref,
    redacted_key,
)

CATALOG = uuid.UUID("11111111-1111-1111-1111-111111111111")


def test_internal_key_is_built_on_the_catalog_id():
    # Deliberately the id, not the slug: renaming a catalog must not orphan its
    # lineage.
    assert asset_key(catalog_id=CATALOG, schema="analytics", table="orders") == (
        f"cat:{CATALOG}/analytics/orders"
    )


def test_external_key_is_namespaced_by_system():
    assert asset_key(system="crm_pg", schema="public", table="customers") == (
        "ext:crm_pg/public/customers"
    )


def test_internal_and_external_keys_never_collide():
    assert internal_ref(CATALOG, "s", "t").key != external_ref(str(CATALOG), "s", "t").key


def test_external_asset_requires_a_system():
    with pytest.raises(ValueError):
        asset_key(schema="public", table="customers")


def test_refs_report_their_kind():
    assert internal_ref(CATALOG, "s", "t").is_external is False
    assert external_ref("crm", "s", "t").is_external is True


def test_redaction_is_stable_and_reveals_nothing():
    key = internal_ref(CATALOG, "secrets", "salaries").key
    assert redacted_key(key) == redacted_key(key)  # same hidden node collapses to one
    redacted = redacted_key(key)
    assert "salaries" not in redacted
    assert "secrets" not in redacted
    assert str(CATALOG) not in redacted


def test_redaction_distinguishes_different_assets():
    a = redacted_key(internal_ref(CATALOG, "s", "a").key)
    b = redacted_key(internal_ref(CATALOG, "s", "b").key)
    assert a != b
