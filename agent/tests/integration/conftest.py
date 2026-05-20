"""Shared fixtures for agent integration tests.

Mirrors `api/tests/integration/conftest.py` so the agent spike can drive
Unity Catalog directly without depending on the api side. Tests are
skipped when UC_BASE_URL is unset or the server is unreachable.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from uuid import uuid4

import httpx
import pytest


@pytest.fixture(scope="session")
def uc_base_url() -> str:
    url = os.getenv("UC_BASE_URL")
    if not url:
        pytest.skip("UC_BASE_URL not set; skipping agent UC integration test")
    try:
        resp = httpx.get(f"{url}/api/2.1/unity-catalog/catalogs", timeout=2.0)
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        pytest.skip(f"Unity Catalog unreachable at {url}: {exc}")
    return url


@pytest.fixture
async def uc_http(uc_base_url: str) -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(base_url=uc_base_url, timeout=10.0) as client:
        yield client


async def _delete_catalog(client: httpx.AsyncClient, name: str) -> None:
    try:
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
async def uc_catalog(uc_http: httpx.AsyncClient) -> AsyncIterator[str]:
    name = f"dh_agt_{uuid4().hex[:10]}"
    resp = await uc_http.post(
        "/api/2.1/unity-catalog/catalogs",
        json={"name": name, "comment": "duckhaven agent spike"},
    )
    resp.raise_for_status()
    try:
        yield name
    finally:
        await _delete_catalog(uc_http, name)


@pytest.fixture
async def uc_schema(uc_http: httpx.AsyncClient, uc_catalog: str) -> AsyncIterator[str]:
    resp = await uc_http.post(
        "/api/2.1/unity-catalog/schemas",
        json={"name": "main", "catalog_name": uc_catalog},
    )
    resp.raise_for_status()
    yield "main"


@pytest.fixture
def backend_root(tmp_path: Path) -> Iterator[Path]:
    """A throwaway local-fs backend root usable as UC storage_location."""

    root = tmp_path / "backend"
    root.mkdir()
    yield root
