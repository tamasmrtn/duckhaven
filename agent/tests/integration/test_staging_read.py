"""Agent reads a presigned staging URL over httpfs with no storage secret.

This is the load-side acceptance check for issue #160: the dlt destination stages
Parquet via a presigned PUT, then the agent runs
``read_parquet('<presigned get_url>')``. The agent holds no staging credential —
all auth is in the URL signature. Env-gated on POLARIS_S3_BUCKET (bundled MinIO),
mirroring ``test_credential_vending``. The Azure-SAS read path is unit-tested
(Azurite has no Entra), see ``api/tests/unit/services/test_staging_presign.py``.
"""

from __future__ import annotations

import os
import uuid

import duckdb
import pytest

pytestmark = pytest.mark.integration


def _minio_client(endpoint: str):  # noqa: ANN202 - boto3 client is untyped
    import boto3
    from botocore.config import Config

    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        region_name=os.getenv("POLARIS_S3_REGION", "us-east-1"),
        aws_access_key_id=os.getenv("MINIO_ROOT_USER", "minioadmin"),
        aws_secret_access_key=os.getenv("MINIO_ROOT_PASSWORD", "minioadmin"),
        config=Config(s3={"addressing_style": "path"}, signature_version="s3v4"),
    )


def test_agent_reads_presigned_staging_url_without_secret(tmp_path) -> None:
    bucket = os.getenv("POLARIS_S3_BUCKET")
    if not bucket:
        pytest.skip("POLARIS_S3_BUCKET not set; skipping staging read integration test")
    bucket = bucket.split("://", 1)[-1].strip("/")
    endpoint = os.getenv("POLARIS_S3_ENDPOINT") or os.getenv("POLARIS_S3_ENDPOINT_INTERNAL")
    assert endpoint, "POLARIS_S3_ENDPOINT[_INTERNAL] required alongside POLARIS_S3_BUCKET"

    # Produce a small Parquet locally (as the dlt client would) and stage it to
    # MinIO. The upload may use a credential; the agent read must not.
    local = tmp_path / "orders.parquet"
    with duckdb.connect() as gen:
        gen.execute(f"COPY (SELECT * FROM range(5) t(id)) TO '{local}' (FORMAT PARQUET)")
    key = f"_staging/{uuid.uuid4()}/orders.parquet"
    s3 = _minio_client(endpoint)
    s3.put_object(Bucket=bucket, Key=key, Body=local.read_bytes())
    get_url = s3.generate_presigned_url(
        "get_object", Params={"Bucket": bucket, "Key": key}, ExpiresIn=300
    )

    # A fresh connection with httpfs but NO S3 secret of any kind — the presigned
    # URL carries all auth. This is exactly what the agent's session connection
    # does for a staged load.
    conn = duckdb.connect()
    try:
        conn.execute("INSTALL httpfs")
        conn.execute("LOAD httpfs")
        rows = conn.execute(f"SELECT count(*) FROM read_parquet('{get_url}')").fetchone()
        assert rows[0] == 5
    finally:
        conn.close()
        s3.delete_object(Bucket=bucket, Key=key)
