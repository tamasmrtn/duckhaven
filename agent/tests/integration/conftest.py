"""Shared fixtures for agent integration tests.

Drives Apache Polaris directly over REST (no dependency on the api
package) so the agent's DuckDB path can be exercised end-to-end. Skipped
when POLARIS_BASE_URL is unset or the server is unreachable.

FILE storage requires Polaris and this process to share a filesystem, so
the catalog's base location is rooted at POLARIS_WAREHOUSE_DIR — a path
that must be identical inside the Polaris container and on the test host
(e.g. a bind-mounted directory). Tests are skipped when it is unset.

NOTE on writes: with Polaris in a container and the agent on the host,
DuckDB cannot write into table directories Polaris created (ownership
differs), so these tests validate the read/attach path. End-to-end writes
require object storage (S3) or a same-user single-host filesystem.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from uuid import uuid4

import httpx
import pytest


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
def warehouse_dir() -> str:
    """Filesystem dir shared (identically) between Polaris and this process."""
    d = os.getenv("POLARIS_WAREHOUSE_DIR")
    if not d:
        pytest.skip("POLARIS_WAREHOUSE_DIR not set; skipping FILE-storage integration test")
    return d.rstrip("/")


@pytest.fixture(scope="session")
def polaris_creds() -> tuple[str, str]:
    return os.getenv("POLARIS_CLIENT_ID", "root"), os.getenv("POLARIS_CLIENT_SECRET", "s3cr3t")


async def _token(client: httpx.AsyncClient, creds: tuple[str, str]) -> str:
    resp = await client.post(
        "/api/catalog/v1/oauth/tokens",
        data={
            "grant_type": "client_credentials",
            "client_id": creds[0],
            "client_secret": creds[1],
            "scope": "PRINCIPAL_ROLE:ALL",
        },
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


@pytest.fixture
async def polaris_catalog(
    polaris_base_url: str, polaris_creds: tuple[str, str], warehouse_dir: str
) -> AsyncIterator[tuple[str, str]]:
    """Create a FILE catalog with data-access grants + an `analytics` namespace
    holding an `events` table. Yields (catalog_name, namespace)."""
    name = f"dh_agt_{uuid4().hex[:10]}"
    base = f"file://{warehouse_dir}/{name}"
    ns = "analytics"
    async with httpx.AsyncClient(base_url=polaris_base_url, timeout=10.0) as c:
        token = await _token(c, polaris_creds)
        h = {"Authorization": f"Bearer {token}", "Polaris-Realm": "POLARIS"}
        await c.post(
            "/api/management/v1/catalogs",
            headers=h,
            json={
                "catalog": {
                    "name": name,
                    "type": "INTERNAL",
                    "readOnly": False,
                    "properties": {"default-base-location": base},
                    "storageConfigInfo": {"storageType": "FILE", "allowedLocations": [base]},
                }
            },
        )
        # Data-access grants (mirrors PolarisClient.ensure_catalog_access).
        await c.post(
            f"/api/management/v1/catalogs/{name}/catalog-roles",
            headers=h,
            json={"catalogRole": {"name": "duckhaven_rw"}},
        )
        await c.put(
            f"/api/management/v1/catalogs/{name}/catalog-roles/duckhaven_rw/grants",
            headers=h,
            json={"grant": {"type": "catalog", "privilege": "CATALOG_MANAGE_CONTENT"}},
        )
        await c.post(
            "/api/management/v1/principal-roles",
            headers=h,
            json={"principalRole": {"name": "duckhaven"}},
        )
        await c.put(
            f"/api/management/v1/principal-roles/duckhaven/catalog-roles/{name}",
            headers=h,
            json={"catalogRole": {"name": "duckhaven_rw"}},
        )
        await c.put(
            f"/api/management/v1/principals/{polaris_creds[0]}/principal-roles",
            headers=h,
            json={"principalRole": {"name": "duckhaven"}},
        )
        await c.post(
            f"/api/catalog/v1/{name}/namespaces",
            headers=h,
            json={"namespace": [ns], "properties": {}},
        )
        await c.post(
            f"/api/catalog/v1/{name}/namespaces/{ns}/tables",
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
        try:
            yield name, ns
        finally:
            try:
                await c.delete(f"/api/management/v1/catalogs/{name}", headers=h)
            except httpx.HTTPError:
                pass
