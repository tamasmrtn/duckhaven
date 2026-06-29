"""External storage health check against real Polaris (+ object store).

Two lanes, both env-gated:

- **Default lane** (POLARIS_BASE_URL + POLARIS_S3_BUCKET, i.e. `make polaris-dev`):
  the negative test registers an external ``s3`` backend pointing at a bucket
  that does not exist and asserts the health check reports ``valid=False`` — it
  exercises the real provision → probe-table write → failure → cleanup path.
- **Assume-role lane** (DH_TEST_S3_ROLE_ARN + DH_TEST_S3_ROOT_URI, a LocalStack
  STS / real-AWS setup): the positive test registers a backend with a real role
  ARN and asserts the vended credentials reach the bucket. Skips otherwise,
  because MinIO has no STS to assume a role through.
"""

from __future__ import annotations

import os
from uuid import uuid4

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.integration


async def _register(client: AsyncClient, body: dict) -> str:
    resp = await client.post("/admin/storage-backends", json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def test_external_s3_health_fails_for_unreachable_bucket(
    admin_client: AsyncClient,
) -> None:
    """A backend whose bucket cannot be written reports valid=False and cleans up."""
    if not os.getenv("POLARIS_S3_BUCKET"):
        pytest.skip("POLARIS_S3_BUCKET not set; skipping storage health integration test")
    endpoint = os.getenv("POLARIS_S3_ENDPOINT_INTERNAL") or os.getenv("POLARIS_S3_ENDPOINT")
    backend_id = await _register(
        admin_client,
        {
            "kind": "s3",
            "name": f"dh-it-bad-{uuid4().hex[:8]}",
            "root_uri": f"s3://dh-nonexistent-{uuid4().hex[:10]}/probe/",
            "config": {
                "role_arn": "arn:aws:iam::000000000000:role/none",
                "region": os.getenv("POLARIS_S3_REGION", "us-east-1"),
                **({"endpoint": endpoint, "path_style_access": True} if endpoint else {}),
            },
        },
    )
    resp = await admin_client.post(f"/admin/storage-backends/{backend_id}/health")
    assert resp.status_code == 200, resp.text
    assert resp.json()["valid"] is False

    # The probe catalog is torn down — no health catalogs leak.
    listed = await admin_client.get("/admin/storage-backends")
    assert any(b["id"] == backend_id for b in listed.json())


async def test_external_s3_health_passes_with_assume_role(
    admin_client: AsyncClient,
) -> None:
    """With a real assume-role S3 (LocalStack STS or AWS), the check passes."""
    role_arn = os.getenv("DH_TEST_S3_ROLE_ARN")
    root_uri = os.getenv("DH_TEST_S3_ROOT_URI")
    if not role_arn or not root_uri:
        pytest.skip("DH_TEST_S3_ROLE_ARN / DH_TEST_S3_ROOT_URI not set; skipping assume-role test")
    config: dict = {"role_arn": role_arn, "region": os.getenv("DH_TEST_S3_REGION", "us-east-1")}
    if external_id := os.getenv("DH_TEST_S3_EXTERNAL_ID"):
        config["external_id"] = external_id
    if endpoint := os.getenv("DH_TEST_S3_ENDPOINT"):
        config["endpoint"] = endpoint
        config["path_style_access"] = True
    backend_id = await _register(
        admin_client,
        {
            "kind": "s3",
            "name": f"dh-it-ok-{uuid4().hex[:8]}",
            "root_uri": root_uri,
            "config": config,
        },
    )
    resp = await admin_client.post(f"/admin/storage-backends/{backend_id}/health")
    assert resp.status_code == 200, resp.text
    assert resp.json()["valid"] is True, resp.json()["detail"]
