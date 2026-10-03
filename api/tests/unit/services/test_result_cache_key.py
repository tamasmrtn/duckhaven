"""The result cache key (`services/result_cache/key.py`)."""

from __future__ import annotations

import dataclasses
import uuid

from api.services.result_cache.key import AttachedCatalog, CacheContext, cache_key

WS = uuid.UUID(int=1)
CONTEXT = CacheContext(
    current_catalog="c1", current_schema="main", runtime_id="1.5", timezone="UTC"
)
ATTACHED = [AttachedCatalog("id-1", "c1", "iceberg_polaris", "sb-1")]


def _key(**changes) -> str:
    args = {"workspace_id": WS, "canonical": "q", "context": CONTEXT, "attached": ATTACHED}
    args.update(changes)
    return cache_key(**args)


def test_the_key_is_stable() -> None:
    assert _key() == _key()
    assert len(_key()) == 64


def test_every_part_of_the_context_changes_the_key() -> None:
    base = _key()
    assert _key(workspace_id=uuid.UUID(int=2)) != base
    assert _key(canonical="other") != base
    for field, value in [
        ("current_catalog", "c2"),
        ("current_schema", "s"),
        ("runtime_id", "2.0"),
        ("timezone", "Europe/Budapest"),
    ]:
        assert _key(context=dataclasses.replace(CONTEXT, **{field: value})) != base
    # A storage-migration cutover moves the catalog to another backend.
    moved = [AttachedCatalog("id-1", "c1", "iceberg_polaris", "sb-2")]
    assert _key(attached=moved) != base
    # Attaching another catalog changes how names could bind.
    assert _key(attached=[*ATTACHED, AttachedCatalog("id-2", "c2", "ducklake", None)]) != base


def test_the_attached_order_does_not_matter() -> None:
    two = [AttachedCatalog("id-2", "c2", "ducklake", None), *ATTACHED]
    assert _key(attached=two) == _key(attached=list(reversed(two)))
