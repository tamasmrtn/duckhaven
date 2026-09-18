"""The catalog descriptor both dispatch paths put on the wire.

The two paths once described a catalog differently — the session path kept the
Iceberg-era fields — so a DuckLake catalog attached as Iceberg and failed
silently. These tests keep them from diverging again.
"""

from __future__ import annotations

import uuid

import pytest

from api.config import settings
from api.models.catalog import KIND_DUCKLAKE, KIND_ICEBERG_POLARIS, Catalog
from api.models.storage_backend import StorageBackend
from api.services.session_credentials import build_catalog_attach, reset_storage_cache


def _catalog(kind: str, slug: str = "raw") -> Catalog:
    cat = Catalog(
        slug=slug,
        name=slug,
        kind=kind,
        polaris_name=slug if kind == KIND_ICEBERG_POLARIS else None,
        metadata_schema=f"cat_{slug}" if kind == KIND_DUCKLAKE else None,
    )
    cat.id = uuid.uuid4()
    cat.storage_backend = StorageBackend(kind="object_store", name="bundled", root_uri="")
    return cat


@pytest.fixture(autouse=True)
def _clean_cache():
    reset_storage_cache()
    yield
    reset_storage_cache()


@pytest.mark.asyncio
async def test_iceberg_carries_no_credentials():
    """Polaris vends on attach, so the control plane mints nothing."""
    entry = await build_catalog_attach(_catalog(KIND_ICEBERG_POLARIS))
    assert entry["kind"] == KIND_ICEBERG_POLARIS
    assert entry["polaris_name"] == "raw"
    assert "meta" not in entry
    assert "storage" not in entry


@pytest.mark.asyncio
async def test_ducklake_carries_both_credentials_and_its_location():
    """DuckLake has no credential vendor, so both are minted here."""
    original = settings.ducklake_agent_user
    settings.ducklake_agent_user = "ducklake_agent"
    try:
        entry = await build_catalog_attach(_catalog(KIND_DUCKLAKE))
    finally:
        settings.ducklake_agent_user = original

    assert entry["kind"] == KIND_DUCKLAKE
    assert entry["metadata_schema"] == "cat_raw"
    assert entry["data_path"].endswith("/raw/")
    assert entry["meta"]["user"] == "ducklake_agent"
    # Scoped per catalog: a workspace attaches every catalog to one connection.
    assert entry["storage"]["scope"] == entry["data_path"]


@pytest.mark.asyncio
async def test_a_ducklake_catalog_never_claims_a_polaris_warehouse():
    """Sending None would make the agent run `ATTACH 'None' ... TYPE ICEBERG`."""
    entry = await build_catalog_attach(_catalog(KIND_DUCKLAKE))
    assert entry["polaris_name"] == ""


@pytest.mark.asyncio
async def test_the_session_path_sends_the_same_descriptor_as_a_query():
    """If these diverge again, DuckLake stops attaching in sessions silently."""
    from api.services.sql_sessions.service import _catalog_descriptors

    catalogs = [_catalog(KIND_ICEBERG_POLARIS, "ice"), _catalog(KIND_DUCKLAKE, "lake")]
    session_side = await _catalog_descriptors(catalogs)
    query_side = [await build_catalog_attach(c) for c in catalogs]
    assert session_side == query_side
    # And it is the DuckLake shape, not just two matching stubs.
    assert session_side[1]["kind"] == KIND_DUCKLAKE
    assert "meta" in session_side[1]


@pytest.mark.asyncio
async def test_external_credentials_are_minted_once_per_catalog(monkeypatch):
    """Per-query minting would put an STS call in front of every statement."""
    import boto3

    calls = {"n": 0}

    class _Sts:
        def assume_role(self, **kw):
            calls["n"] += 1
            return {
                "Credentials": {
                    "AccessKeyId": "a",
                    "SecretAccessKey": "b",
                    "SessionToken": "c",
                }
            }

    monkeypatch.setattr(boto3, "client", lambda *a, **k: _Sts())
    cat = _catalog(KIND_DUCKLAKE)
    cat.storage_backend = StorageBackend(
        kind="s3",
        name="corp",
        root_uri="s3://corp/lake",
        config={"role_arn": "arn:x", "region": "us-east-1"},
    )
    for _ in range(3):
        await build_catalog_attach(cat)
    assert calls["n"] == 1

    reset_storage_cache()
    await build_catalog_attach(cat)
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_the_bundled_store_is_not_cached_because_it_mints_nothing():
    """It reads settings, so caching could only serve a stale endpoint."""
    cat = _catalog(KIND_DUCKLAKE)
    await build_catalog_attach(cat)
    assert (
        cat.id
        not in __import__(
            "api.services.session_credentials", fromlist=["_storage_cache"]
        )._storage_cache
    )
