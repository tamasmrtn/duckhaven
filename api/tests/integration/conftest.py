"""Shared fixtures for api integration tests.

These tests require a live Apache Polaris instance reachable at the URL
named in the `POLARIS_BASE_URL` environment variable (with
`POLARIS_CLIENT_ID` / `POLARIS_CLIENT_SECRET`). When unset (the default
on dev machines without `docker compose up`), every test is skipped.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator
from uuid import uuid4

import httpx
import pytest

from api.services.polaris import PolarisClient


@pytest.fixture(scope="session")
def polaris_base_url() -> str:
    """Resolve POLARIS_BASE_URL or skip. Probes the health endpoint to fail fast."""
    url = os.getenv("POLARIS_BASE_URL")
    if not url:
        pytest.skip("POLARIS_BASE_URL not set; skipping Polaris integration test")
    # Health/metrics live on the management port (8182), the API on 8181.
    health = url.replace(":8181", ":8182").rstrip("/") + "/q/health"
    try:
        httpx.get(health, timeout=2.0).raise_for_status()
    except httpx.HTTPError as exc:
        pytest.skip(f"Polaris unreachable at {url}: {exc}")
    return url


@pytest.fixture
async def polaris(polaris_base_url: str) -> AsyncIterator[PolarisClient]:
    """A PolarisClient pointed at the live server, using env credentials."""
    client = PolarisClient(
        base_url=polaris_base_url,
        realm=os.getenv("POLARIS_REALM", "POLARIS"),
        client_id=os.getenv("POLARIS_CLIENT_ID", "root"),
        client_secret=os.getenv("POLARIS_CLIENT_SECRET", "s3cr3t"),
        timeout_s=10.0,
    )
    try:
        yield client
    finally:
        await client.aclose()


@pytest.fixture
def s3_catalog_storage() -> tuple[str, dict]:
    """(bucket base, extra storageConfigInfo) for a bundled-MinIO (S3) catalog.

    Skips when POLARIS_S3_BUCKET is unset. Polaris is object-storage only, so
    every integration catalog is S3-backed (see ADR 0001)."""
    bucket = os.getenv("POLARIS_S3_BUCKET")
    if not bucket:
        pytest.skip("POLARIS_S3_BUCKET not set; skipping Polaris integration test")
    extra: dict = {"region": os.getenv("POLARIS_S3_REGION", "us-east-1")}
    if endpoint := os.getenv("POLARIS_S3_ENDPOINT"):
        extra["endpoint"] = endpoint
        extra["pathStyleAccess"] = True
    if internal := os.getenv("POLARIS_S3_ENDPOINT_INTERNAL"):
        extra["endpointInternal"] = internal
    return bucket.rstrip("/"), extra


@pytest.fixture
async def unique_catalog(
    polaris: PolarisClient, s3_catalog_storage: tuple[str, dict]
) -> AsyncIterator[str]:
    """Create a uniquely-named S3 (bundled MinIO) catalog; tear it down on exit."""
    bucket, extra = s3_catalog_storage
    name = f"dh_it_{uuid4().hex[:12]}"
    base = f"{bucket}/{name}"
    await polaris.create_catalog(name, storage_type="S3", base_location=base, extra_storage=extra)
    try:
        yield name
    finally:
        try:
            await polaris.delete_catalog(name)
        except Exception:  # noqa: BLE001 - best-effort teardown
            pass


@pytest.fixture
def unique_name() -> Iterator[str]:
    """A short unique identifier safe to use as a catalog object name."""
    yield f"dh_{uuid4().hex[:10]}"
