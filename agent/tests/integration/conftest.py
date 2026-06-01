"""Shared fixtures for agent integration tests.

Drives Apache Polaris directly over REST (no dependency on the api
package) so the agent's DuckDB path can be exercised end-to-end. Skipped
when POLARIS_BASE_URL is unset or the server is unreachable.

NOTE: these tests use FILE storage, so Polaris and this process must share
a filesystem (single-host). They will not pass against a Polaris running in
an isolated container whose `/tmp` differs from the test runner's.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
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
def polaris_creds() -> tuple[str, str]:
    return os.getenv("POLARIS_CLIENT_ID", "root"), os.getenv("POLARIS_CLIENT_SECRET", "s3cr3t")


@pytest.fixture
async def polaris_token(polaris_base_url: str, polaris_creds: tuple[str, str]) -> str:
    client_id, client_secret = polaris_creds
    async with httpx.AsyncClient(base_url=polaris_base_url, timeout=10.0) as c:
        resp = await c.post(
            "/api/catalog/v1/oauth/tokens",
            data={
                "grant_type": "client_credentials",
                "client_id": client_id,
                "client_secret": client_secret,
                "scope": "PRINCIPAL_ROLE:ALL",
            },
        )
        resp.raise_for_status()
        return resp.json()["access_token"]


@pytest.fixture
async def polaris_catalog(
    polaris_base_url: str, polaris_token: str, tmp_path: Path
) -> AsyncIterator[str]:
    """Create a FILE-storage catalog rooted under a shared tmp dir; tear down."""
    name = f"dh_agt_{uuid4().hex[:10]}"
    base = (tmp_path / name).as_uri()
    headers = {"Authorization": f"Bearer {polaris_token}", "Polaris-Realm": "POLARIS"}
    async with httpx.AsyncClient(base_url=polaris_base_url, timeout=10.0) as c:
        resp = await c.post(
            "/api/management/v1/catalogs",
            headers=headers,
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
        resp.raise_for_status()
        await c.post(
            f"/api/catalog/v1/{name}/namespaces",
            headers=headers,
            json={"namespace": ["main"], "properties": {}},
        )
        try:
            yield name
        finally:
            try:
                await c.delete(f"/api/management/v1/catalogs/{name}", headers=headers)
            except httpx.HTTPError:
                pass


@pytest.fixture
def backend_root(tmp_path: Path) -> Iterator[Path]:
    """A throwaway local-fs backend root."""
    root = tmp_path / "backend"
    root.mkdir()
    yield root
