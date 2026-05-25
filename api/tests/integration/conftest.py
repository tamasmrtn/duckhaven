"""Shared fixtures for api integration tests.

These tests require a live Unity Catalog OSS instance reachable at the URL
named in the `UC_BASE_URL` environment variable. When unset (the default
on dev machines without `docker compose up`), every test is skipped.

The fixtures here drive the UC REST API directly so the M3 spikes can
validate UC behaviour before the production UCClient wrapper lands in
Step 5.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator
from uuid import uuid4

import httpx
import pytest


@pytest.fixture(scope="session")
def uc_base_url() -> str:
    """Resolve UC_BASE_URL or skip the test. Probes once to fail fast."""

    url = os.getenv("UC_BASE_URL")
    if not url:
        pytest.skip("UC_BASE_URL not set; skipping UC integration test")
    try:
        resp = httpx.get(f"{url}/api/2.1/unity-catalog/catalogs", timeout=2.0)
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        pytest.skip(f"Unity Catalog unreachable at {url}: {exc}")
    return url


@pytest.fixture
async def uc_http(uc_base_url: str) -> AsyncIterator[httpx.AsyncClient]:
    """Async httpx client pointed at the UC REST API."""

    async with httpx.AsyncClient(base_url=uc_base_url, timeout=10.0) as client:
        yield client


async def _delete_catalog(client: httpx.AsyncClient, name: str) -> None:
    """Force-delete a catalog and ignore any errors (best-effort teardown)."""

    try:
        # Drop any tables under any schemas first (UC 0.4 doesn't always
        # cascade on `force=true`).
        schemas = await client.get("/api/2.1/unity-catalog/schemas", params={"catalog_name": name})
        for schema in schemas.json().get("schemas", []) or []:
            schema_name = schema["name"]
            tables = await client.get(
                "/api/2.1/unity-catalog/tables",
                params={"catalog_name": name, "schema_name": schema_name},
            )
            for table in tables.json().get("tables", []) or []:
                await client.delete(
                    f"/api/2.1/unity-catalog/tables/{name}.{schema_name}.{table['name']}"
                )
            await client.delete(
                f"/api/2.1/unity-catalog/schemas/{name}.{schema_name}",
                params={"force": "true"},
            )
        await client.delete(f"/api/2.1/unity-catalog/catalogs/{name}", params={"force": "true"})
    except httpx.HTTPError:
        pass


@pytest.fixture
async def unique_catalog(uc_http: httpx.AsyncClient) -> AsyncIterator[str]:
    """Create a uniquely-named catalog for the test; tear it down on exit."""

    name = f"dh_it_{uuid4().hex[:12]}"
    resp = await uc_http.post(
        "/api/2.1/unity-catalog/catalogs",
        json={"name": name, "comment": "duckhaven integration test"},
    )
    resp.raise_for_status()
    try:
        yield name
    finally:
        await _delete_catalog(uc_http, name)


@pytest.fixture
def unique_name() -> Iterator[str]:
    """A short unique identifier safe to use as a UC object name."""

    yield f"dh_{uuid4().hex[:10]}"
