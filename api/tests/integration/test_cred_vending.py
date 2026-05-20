"""Spike S5 — Unity Catalog temporary-table-credentials endpoint.

What this spike has to answer:
1. What is the response shape of `POST /temporary-table-credentials` for
   S3-backed tables? (Drives the `storage_credentials` payload that
   `dispatch_query` will embed in DISPATCH_QUERY in Step 7.)
2. What does UC do when asked to vend creds for a local-fs table? The
   api short-circuits in that case (no creds needed); the spike just
   confirms UC's behaviour so the short-circuit is justified.
3. How long is the vended TTL? Sizes `cred_safety_window_s` (default =
   max(300, vended_ttl/2)) in Step 7.

The cache half-TTL refresh behaviour is pure Python and is unit-tested
in Step 7 (`api/tests/unit/services/test_uc_credentials.py`).
"""

from __future__ import annotations

import os
from datetime import UTC, datetime

import httpx
import pytest

pytestmark = pytest.mark.integration


async def _create_managed_delta_table(
    uc_http: httpx.AsyncClient,
    catalog: str,
    schema: str,
    name: str,
    storage_location: str,
) -> dict:
    body = {
        "name": name,
        "catalog_name": catalog,
        "schema_name": schema,
        "table_type": "MANAGED",
        "data_source_format": "DELTA",
        "storage_location": storage_location,
        "columns": [
            {
                "name": "id",
                "type_text": "int",
                "type_json": '{"name":"id","type":"integer","nullable":false,"metadata":{}}',
                "type_name": "INT",
                "type_precision": 0,
                "type_scale": 0,
                "type_interval_type": None,
                "position": 0,
                "nullable": False,
                "comment": None,
            }
        ],
        "properties": {"delta.feature.catalogManaged": "supported"},
        "comment": "duckhaven spike S5",
    }
    resp = await uc_http.post("/api/2.1/unity-catalog/tables", json=body)
    resp.raise_for_status()
    return resp.json()


async def _create_schema(uc_http: httpx.AsyncClient, catalog: str, name: str) -> None:
    resp = await uc_http.post(
        "/api/2.1/unity-catalog/schemas",
        json={"name": name, "catalog_name": catalog},
    )
    resp.raise_for_status()


async def test_local_fs_vending_is_refused_or_empty(
    uc_http: httpx.AsyncClient, unique_catalog: str, tmp_path
) -> None:
    """Local-fs backends shouldn't get creds; api must short-circuit.

    Spike outcome we care about: confirming UC either refuses (4xx) or
    returns an empty credential block for a file:// storage location.
    Either answer is fine for our code; we just need to know which so
    the api's local-fs short-circuit in Step 7 is justified by behaviour
    rather than guess.
    """

    await _create_schema(uc_http, unique_catalog, "main")
    table_root = tmp_path / "local_table"
    table_root.mkdir()
    table = await _create_managed_delta_table(
        uc_http, unique_catalog, "main", "events", table_root.as_uri()
    )

    resp = await uc_http.post(
        "/api/2.1/unity-catalog/temporary-table-credentials",
        json={"table_id": table["table_id"], "operation": "READ_WRITE"},
    )

    # Either UC refuses entirely (4xx) or vends an empty/local-fs creds
    # response. Both are acceptable; the api will treat both as "no
    # creds needed" for local backends.
    if resp.status_code >= 400:
        return
    body = resp.json()
    cloud_keys = {"aws_temp_credentials", "azure_user_delegation_sas", "gcp_oauth_token"}
    assert not (cloud_keys & body.keys()), (
        f"UC unexpectedly vended cloud creds for a file:// table: {body}"
    )


@pytest.mark.skipif(
    not os.getenv("M3_S3_BUCKET"),
    reason="M3_S3_BUCKET not set; skipping S3 cred-vending spike",
)
async def test_s3_vending_returns_aws_creds_with_expiry(
    uc_http: httpx.AsyncClient, unique_catalog: str
) -> None:
    """The S3 happy path: UC vends `aws_temp_credentials` with a future
    `expiration_time`. This is the response shape we'll thread through
    DISPATCH_QUERY's `storage_credentials` field."""

    await _create_schema(uc_http, unique_catalog, "main")
    bucket = os.environ["M3_S3_BUCKET"]
    storage_location = f"s3://{bucket}/duckhaven-spike/{unique_catalog}/main/events_s3/"
    table = await _create_managed_delta_table(
        uc_http, unique_catalog, "main", "events_s3", storage_location
    )

    resp = await uc_http.post(
        "/api/2.1/unity-catalog/temporary-table-credentials",
        json={"table_id": table["table_id"], "operation": "READ_WRITE"},
    )
    resp.raise_for_status()
    body = resp.json()

    aws = body.get("aws_temp_credentials")
    assert aws, f"Expected aws_temp_credentials in {body}"
    assert aws.get("access_key_id")
    assert aws.get("secret_access_key")

    exp_raw = body.get("expiration_time") or aws.get("expiration_time")
    assert exp_raw, "Missing expiration_time in cred response"
    # UC OSS expresses this as either an ISO timestamp or a millis epoch.
    if isinstance(exp_raw, (int, float)):
        exp = datetime.fromtimestamp(exp_raw / 1000, tz=UTC)
    else:
        exp = datetime.fromisoformat(exp_raw.replace("Z", "+00:00"))
    assert exp > datetime.now(tz=UTC)


@pytest.mark.skipif(
    not (os.getenv("M3_ADLS_ACCOUNT") and os.getenv("M3_ADLS_CONTAINER")),
    reason="M3_ADLS_* not fully set; skipping ADLS cred-vending spike",
)
async def test_adls_vending_returns_sas(uc_http: httpx.AsyncClient, unique_catalog: str) -> None:
    await _create_schema(uc_http, unique_catalog, "main")
    account = os.environ["M3_ADLS_ACCOUNT"]
    container = os.environ["M3_ADLS_CONTAINER"]
    storage_location = (
        f"abfss://{container}@{account}.dfs.core.windows.net/"
        f"duckhaven-spike/{unique_catalog}/main/events_adls/"
    )
    table = await _create_managed_delta_table(
        uc_http, unique_catalog, "main", "events_adls", storage_location
    )

    resp = await uc_http.post(
        "/api/2.1/unity-catalog/temporary-table-credentials",
        json={"table_id": table["table_id"], "operation": "READ_WRITE"},
    )
    resp.raise_for_status()
    body = resp.json()
    assert body.get("azure_user_delegation_sas"), f"Expected azure_user_delegation_sas in {body}"
