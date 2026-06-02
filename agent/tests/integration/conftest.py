"""Shared fixtures for agent integration tests.

Drives Apache Polaris directly over REST (no dependency on the api
package) so the agent's DuckDB path can be exercised end-to-end. Skipped
when POLARIS_BASE_URL is unset or the server is unreachable.

Polaris is object-storage only (see ADR 0001). The `polaris_s3_catalog`
fixture creates an S3-backed catalog and requires POLARIS_S3_BUCKET (+
POLARIS_S3_ENDPOINT[_INTERNAL]). It supports both reads and `INSERT`, since
Polaris vends scoped object-store credentials to DuckDB. `make polaris-dev`
provides a local MinIO-backed stack.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from typing import Any
from uuid import uuid4

import httpx
import pytest

CATALOG_API = "/api/catalog/v1"
MGMT_API = "/api/management/v1"
NAMESPACE = "analytics"


@pytest.fixture(scope="session")
def polaris_base_url() -> str:
    url = os.getenv("POLARIS_BASE_URL")
    if not url:
        pytest.skip("POLARIS_BASE_URL not set; skipping agent Polaris integration test")
    health = url.replace(":8181", ":8182").rstrip("/") + "/q/health"
    try:
        httpx.get(health, timeout=2.0).raise_for_status()
    except httpx.HTTPError as exc:
        pytest.skip(f"Polaris unreachable at {url}: {exc}")
    return url


@pytest.fixture(scope="session")
def polaris_creds() -> tuple[str, str]:
    return os.getenv("POLARIS_CLIENT_ID", "root"), os.getenv("POLARIS_CLIENT_SECRET", "s3cr3t")


async def _token(client: httpx.AsyncClient, creds: tuple[str, str]) -> str:
    resp = await client.post(
        f"{CATALOG_API}/oauth/tokens",
        data={
            "grant_type": "client_credentials",
            "client_id": creds[0],
            "client_secret": creds[1],
            "scope": "PRINCIPAL_ROLE:ALL",
        },
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


async def _provision(
    c: httpx.AsyncClient,
    h: dict[str, str],
    name: str,
    base_location: str,
    storage_config: dict[str, Any],
    principal: str,
) -> None:
    """Create a catalog with data-access grants + an `analytics` namespace
    holding an `events` table (mirrors PolarisClient + ensure_catalog_access)."""
    await c.post(
        f"{MGMT_API}/catalogs",
        headers=h,
        json={
            "catalog": {
                "name": name,
                "type": "INTERNAL",
                "readOnly": False,
                "properties": {
                    "default-base-location": base_location,
                    "polaris.config.drop-with-purge.enabled": "true",
                },
                "storageConfigInfo": storage_config,
            }
        },
    )
    await c.post(
        f"{MGMT_API}/catalogs/{name}/catalog-roles",
        headers=h,
        json={"catalogRole": {"name": "duckhaven_rw"}},
    )
    for privilege in ("CATALOG_MANAGE_CONTENT", "CATALOG_MANAGE_METADATA", "CATALOG_MANAGE_ACCESS"):
        await c.put(
            f"{MGMT_API}/catalogs/{name}/catalog-roles/duckhaven_rw/grants",
            headers=h,
            json={"grant": {"type": "catalog", "privilege": privilege}},
        )
    await c.post(
        f"{MGMT_API}/principal-roles", headers=h, json={"principalRole": {"name": "duckhaven"}}
    )
    await c.put(
        f"{MGMT_API}/principal-roles/duckhaven/catalog-roles/{name}",
        headers=h,
        json={"catalogRole": {"name": "duckhaven_rw"}},
    )
    await c.put(
        f"{MGMT_API}/principals/{principal}/principal-roles",
        headers=h,
        json={"principalRole": {"name": "duckhaven"}},
    )
    await c.post(f"{CATALOG_API}/{name}/namespaces", headers=h, json={"namespace": [NAMESPACE]})
    await c.post(
        f"{CATALOG_API}/{name}/namespaces/{NAMESPACE}/tables",
        headers=h,
        json={
            "name": "events",
            "schema": {
                "type": "struct",
                "schema-id": 0,
                "fields": [
                    {"id": 1, "name": "id", "required": False, "type": "long"},
                    {"id": 2, "name": "label", "required": False, "type": "string"},
                ],
            },
        },
    )


async def _make_catalog(
    polaris_base_url: str,
    creds: tuple[str, str],
    base_location: str,
    storage_config: dict[str, Any],
) -> AsyncIterator[tuple[str, str]]:
    name = f"dh_agt_{uuid4().hex[:10]}"
    async with httpx.AsyncClient(base_url=polaris_base_url, timeout=15.0) as c:
        h = {"Authorization": f"Bearer {await _token(c, creds)}", "Polaris-Realm": "POLARIS"}
        await _provision(c, h, name, base_location, storage_config, creds[0])
        try:
            yield name, NAMESPACE
        finally:
            try:
                await c.delete(f"{MGMT_API}/catalogs/{name}", headers=h)
            except httpx.HTTPError:
                pass


@pytest.fixture
async def polaris_s3_catalog(
    polaris_base_url: str, polaris_creds: tuple[str, str]
) -> AsyncIterator[tuple[str, str]]:
    """S3-backed catalog (object storage supports writes via vended creds).
    Requires POLARIS_S3_BUCKET (+ POLARIS_S3_ENDPOINT[_INTERNAL])."""
    bucket = os.getenv("POLARIS_S3_BUCKET")
    if not bucket:
        pytest.skip("POLARIS_S3_BUCKET not set; skipping S3 write integration test")
    base = f"{bucket.rstrip('/')}/{uuid4().hex[:8]}"
    storage: dict[str, Any] = {
        "storageType": "S3",
        "allowedLocations": [base],
        "region": os.getenv("POLARIS_S3_REGION", "us-east-1"),
    }
    if endpoint := os.getenv("POLARIS_S3_ENDPOINT"):
        storage["endpoint"] = endpoint
        storage["pathStyleAccess"] = True
    if internal := os.getenv("POLARIS_S3_ENDPOINT_INTERNAL"):
        storage["endpointInternal"] = internal
    async for cat in _make_catalog(polaris_base_url, polaris_creds, base, storage):
        yield cat
