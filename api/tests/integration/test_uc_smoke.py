"""Spike S2 — Unity Catalog OSS reachability + catalog/schema CRUD.

Validates the basic UC REST shape we depend on in M3 before we wrap it in
`api.services.unity_catalog.UCClient`. If any of these tests fail or
behave differently than expected, the wrapper's contract must be
adjusted (Step 5).
"""

from __future__ import annotations

import httpx
import pytest

pytestmark = pytest.mark.integration


async def test_get_catalog_after_create(uc_http: httpx.AsyncClient, unique_catalog: str) -> None:
    resp = await uc_http.get(f"/api/2.1/unity-catalog/catalogs/{unique_catalog}")
    resp.raise_for_status()
    body = resp.json()
    assert body["name"] == unique_catalog


async def test_create_catalog_duplicate_is_conflict(
    uc_http: httpx.AsyncClient, unique_catalog: str
) -> None:
    """UC OSS returns 409 (or 400 with detail) on duplicate; caller decides
    idempotency. The wrapper in Step 5 will swallow this and treat it as
    a no-op when needed."""

    resp = await uc_http.post(
        "/api/2.1/unity-catalog/catalogs",
        json={"name": unique_catalog, "comment": "duplicate"},
    )
    assert resp.status_code in (400, 409)


async def test_create_schema_in_catalog(uc_http: httpx.AsyncClient, unique_catalog: str) -> None:
    resp = await uc_http.post(
        "/api/2.1/unity-catalog/schemas",
        json={"name": "main", "catalog_name": unique_catalog},
    )
    resp.raise_for_status()
    body = resp.json()
    assert body["name"] == "main"
    assert body["catalog_name"] == unique_catalog


async def test_list_tables_in_empty_schema(uc_http: httpx.AsyncClient, unique_catalog: str) -> None:
    await uc_http.post(
        "/api/2.1/unity-catalog/schemas",
        json={"name": "main", "catalog_name": unique_catalog},
    )
    resp = await uc_http.get(
        "/api/2.1/unity-catalog/tables",
        params={"catalog_name": unique_catalog, "schema_name": "main"},
    )
    resp.raise_for_status()
    body = resp.json()
    tables = body.get("tables") or []
    assert tables == []


async def test_list_schemas_after_create(uc_http: httpx.AsyncClient, unique_catalog: str) -> None:
    await uc_http.post(
        "/api/2.1/unity-catalog/schemas",
        json={"name": "main", "catalog_name": unique_catalog},
    )
    resp = await uc_http.get(
        "/api/2.1/unity-catalog/schemas",
        params={"catalog_name": unique_catalog},
    )
    resp.raise_for_status()
    schemas = resp.json().get("schemas") or []
    assert any(s["name"] == "main" for s in schemas)
