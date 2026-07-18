"""Presigned staging round-trip against the bundled MinIO (issue #160).

Env-gated on POLARIS_S3_BUCKET (i.e. `make polaris-dev`), like the storage-health
integration test. External s3 / Azure presigning needs real cloud (STS / AAD) and
is only unit-tested (MinIO has no STS, Azurite no Entra) — see
``tests/unit/services/test_staging_presign.py``.
"""

from __future__ import annotations

import os
import uuid
from types import SimpleNamespace

import httpx
import pytest

from api.config import settings
from api.services import staging_presign

pytestmark = pytest.mark.integration


def _object_store_catalog() -> SimpleNamespace:
    # root_uri="" -> the bundled bucket root; staging_uri_for resolves it via
    # polaris_storage to s3://<bucket>/_staging/<session_id>/.
    return SimpleNamespace(
        storage_backend=SimpleNamespace(kind="object_store", root_uri="", config=None)
    )


async def test_presigned_put_get_roundtrip_on_minio(monkeypatch) -> None:
    """A presigned PUT uploads bytes and the presigned GET reads them back —
    proving the real boto3 SigV4 signature is accepted by MinIO end to end."""
    if not os.getenv("POLARIS_S3_BUCKET"):
        pytest.skip("POLARIS_S3_BUCKET not set; skipping staging presign integration test")
    # The test process reaches MinIO at the external endpoint; the get_url is
    # normally signed for the in-network agent endpoint, so point that at the same
    # reachable endpoint for the off-network round-trip.
    monkeypatch.setattr(settings, "s3_endpoint_internal", settings.s3_endpoint)

    files, _expires = staging_presign.presign_staging_files(
        _object_store_catalog(), uuid.uuid4(), ["probe.parquet"], ttl_s=300
    )
    staged = files[0]
    payload = b"duckhaven-staging-probe"

    async with httpx.AsyncClient(timeout=10.0) as client:
        put = await client.put(staged.put_url, content=payload)
        assert put.status_code in (200, 201), put.text
        got = await client.get(staged.get_url)
        assert got.status_code == 200, got.text
        assert got.content == payload
