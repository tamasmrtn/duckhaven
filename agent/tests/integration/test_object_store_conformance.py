"""S3 semantics the bundled object store must provide, asserted directly.

Everything else in this suite exercises storage *through* Polaris and Iceberg,
which means a storage regression reaches us as a confusing Iceberg error — or,
worse, as silently wrong data. These tests skip all of that and check the six
behaviours DuckDB's httpfs actually depends on, against whatever store the
deployment bundles.

They exist because the bundled store was swapped from MinIO to RustFS while
RustFS was still pre-GA. The specific hazards each case covers:

- A ranged read that is short, truncated or mis-bounded corrupts a Parquet read
  rather than failing it. DuckDB issues a HEAD for ``Accept-Ranges`` and then a
  burst of ranged GETs for the footer and individual column chunks; if the HEAD
  does not advertise ranges, DuckDB will not do partial reads at all.
- A write that is not immediately readable corrupts an Iceberg commit, which
  reads its own manifest back straight after writing it.
- ``ListObjectsV2`` must honour a trailing-slash prefix, which is what
  ``api/services/migration/storage_io`` relies on and what a scoped
  ``s3:prefix`` policy requires.

Env-gated on ``POLARIS_S3_BUCKET`` like the rest of the suite; `make polaris-dev`
provides a local object-store-backed stack.
"""

from __future__ import annotations

import hashlib
import os
import uuid

import duckdb
import httpx
import pytest

pytestmark = pytest.mark.integration

# A range covering the whole object plus one byte past the end. S3 answers 416
# for a range that starts at or beyond the object length.
_PAST_THE_END = 10


@pytest.fixture(scope="module")
def bucket() -> str:
    value = os.getenv("POLARIS_S3_BUCKET")
    if not value:
        pytest.skip("POLARIS_S3_BUCKET not set; skipping object-store conformance tests")
    return value.split("://", 1)[-1].strip("/")


@pytest.fixture(scope="module")
def endpoint() -> str:
    value = os.getenv("POLARIS_S3_ENDPOINT") or os.getenv("POLARIS_S3_ENDPOINT_INTERNAL")
    if not value:
        pytest.skip("POLARIS_S3_ENDPOINT[_INTERNAL] not set")
    return value


@pytest.fixture(scope="module")
def s3(endpoint: str):  # noqa: ANN201 - boto3 client is untyped
    """Path-style SigV4, matching how the API presigns and how Polaris vends."""
    import boto3
    from botocore.config import Config

    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        region_name=os.getenv("POLARIS_S3_REGION", "us-east-1"),
        aws_access_key_id=os.getenv("OBJECT_STORE_ACCESS_KEY", "duckhaven"),
        aws_secret_access_key=os.getenv("OBJECT_STORE_SECRET_KEY", "duckhaven"),
        config=Config(s3={"addressing_style": "path"}, signature_version="s3v4"),
    )


@pytest.fixture
def prefix(s3, bucket: str):  # noqa: ANN201
    """A unique prefix per test, deleted afterwards."""
    key_prefix = f"_conformance/{uuid.uuid4()}/"
    yield key_prefix
    listing = s3.list_objects_v2(Bucket=bucket, Prefix=key_prefix)
    for obj in listing.get("Contents", []):
        s3.delete_object(Bucket=bucket, Key=obj["Key"])


@pytest.fixture(scope="module")
def parquet_bytes(tmp_path_factory) -> bytes:
    """A real Parquet file, so the ranged reads land on real footers and chunks."""
    path = tmp_path_factory.mktemp("conformance") / "probe.parquet"
    with duckdb.connect() as con:
        con.execute(
            f"COPY (SELECT i AS id, 'row-' || i AS label FROM range(50000) t(i)) "
            f"TO '{path}' (FORMAT PARQUET)"
        )
    return path.read_bytes()


def test_head_advertises_byte_ranges(s3, bucket, prefix, parquet_bytes):
    """DuckDB probes Accept-Ranges before it will read a Parquet file in parts."""
    key = f"{prefix}probe.parquet"
    s3.put_object(Bucket=bucket, Key=key, Body=parquet_bytes)

    head = s3.head_object(Bucket=bucket, Key=key)

    assert head["ContentLength"] == len(parquet_bytes)
    assert head.get("AcceptRanges") == "bytes"


def test_ranged_gets_return_exact_bytes(s3, bucket, prefix, parquet_bytes):
    """Footer, interior, suffix and open-ended ranges must all be byte-exact.

    A short or shifted range does not raise — it yields wrong data, which is why
    each case compares against the local bytes rather than just a length.
    """
    key = f"{prefix}probe.parquet"
    size = len(parquet_bytes)
    s3.put_object(Bucket=bucket, Key=key, Body=parquet_bytes)

    def fetch(spec: str) -> bytes:
        return s3.get_object(Bucket=bucket, Key=key, Range=spec)["Body"].read()

    # The footer is the first thing DuckDB reads, and it ends in the magic bytes.
    footer = fetch(f"bytes={size - 8}-{size - 1}")
    assert footer == parquet_bytes[-8:]
    assert footer.endswith(b"PAR1")

    assert fetch("bytes=1024-2047") == parquet_bytes[1024:2048]
    assert fetch("bytes=-100") == parquet_bytes[-100:]
    assert fetch("bytes=100-") == parquet_bytes[100:]


def test_range_past_the_end_is_refused(s3, bucket, prefix):
    """416, not a 200 with a truncated or empty body that reads as valid data."""
    key = f"{prefix}small.bin"
    body = b"0123456789"
    s3.put_object(Bucket=bucket, Key=key, Body=body)
    url = s3.generate_presigned_url(
        "get_object", Params={"Bucket": bucket, "Key": key}, ExpiresIn=60
    )

    resp = httpx.get(url, headers={"Range": f"bytes={len(body)}-{len(body) + _PAST_THE_END}"})

    assert resp.status_code == 416


def test_object_is_readable_immediately_after_the_write(s3, bucket, prefix):
    """An Iceberg commit reads back the manifest it just wrote.

    A store that acknowledges a PUT before the object is durably readable
    corrupts that commit, so this writes and reads with no delay between them
    and compares bytes rather than just checking for a 200.
    """
    mismatches = []
    for i in range(25):
        key = f"{prefix}race/{i}.bin"
        payload = hashlib.sha256(str(i).encode()).digest() * 512  # 16 KiB
        s3.put_object(Bucket=bucket, Key=key, Body=payload)
        if s3.get_object(Bucket=bucket, Key=key)["Body"].read() != payload:
            mismatches.append(key)

    assert mismatches == []


def test_listing_honours_a_trailing_slash_prefix(s3, bucket, prefix):
    """``storage_io`` always sends a trailing slash, because a scoped
    ``s3:prefix`` policy only matches a request prefix that has one."""
    for i in range(3):
        s3.put_object(Bucket=bucket, Key=f"{prefix}listed/{i}.bin", Body=b"x")
    s3.put_object(Bucket=bucket, Key=f"{prefix}other.bin", Body=b"x")

    listing = s3.list_objects_v2(Bucket=bucket, Prefix=f"{prefix}listed/")

    assert listing["KeyCount"] == 3
    assert all(k["Key"].startswith(f"{prefix}listed/") for k in listing["Contents"])


def test_presigned_put_and_get_are_accepted(s3, bucket, prefix):
    """SQL-session staging presigns a plain single-part PUT and a GET, with no
    checksum headers and no multipart — the store must accept exactly that."""
    key = f"{prefix}staged.bin"
    body = b"duckhaven staging payload"

    put_url = s3.generate_presigned_url(
        "put_object", Params={"Bucket": bucket, "Key": key}, ExpiresIn=60
    )
    put = httpx.put(put_url, content=body)
    assert put.status_code == 200, put.text

    get_url = s3.generate_presigned_url(
        "get_object", Params={"Bucket": bucket, "Key": key}, ExpiresIn=60
    )
    assert httpx.get(get_url).content == body
    # The agent reads staged Parquet in parts, so a presigned URL must range too.
    partial = httpx.get(get_url, headers={"Range": "bytes=0-8"})
    assert partial.status_code == 206
    assert partial.content == body[:9]


def test_duckdb_httpfs_reads_and_writes_through_the_store(s3, bucket, prefix, endpoint):
    """The whole hot path in one statement pair, as the agent runs it.

    ``COPY ... TO`` exercises DuckDB's multipart writer (the object is over the
    5 MiB part threshold) and ``read_parquet`` exercises the ranged-read path,
    including the column pruning that turns a scan into selective GETs.
    """
    con = duckdb.connect()
    try:
        con.execute("INSTALL httpfs")
        con.execute("LOAD httpfs")
        host = endpoint.split("://", 1)[-1]
        use_ssl = "true" if endpoint.startswith("https://") else "false"
        con.execute(
            "CREATE SECRET conformance (TYPE S3, KEY_ID ?, SECRET ?, ENDPOINT ?, "
            f"URL_STYLE 'path', USE_SSL {use_ssl}, REGION ?)",
            [
                os.getenv("OBJECT_STORE_ACCESS_KEY", "duckhaven"),
                os.getenv("OBJECT_STORE_SECRET_KEY", "duckhaven"),
                host,
                os.getenv("POLARIS_S3_REGION", "us-east-1"),
            ],
        )
        uri = f"s3://{bucket}/{prefix}written.parquet"
        con.execute(
            f"COPY (SELECT i AS id, repeat('p', 200) AS pad FROM range(60000) t(i)) "
            f"TO '{uri}' (FORMAT PARQUET)"
        )

        def scalar(sql: str) -> object:
            return con.execute(sql).fetchone()[0]

        assert scalar(f"SELECT count(*) FROM read_parquet('{uri}')") == 60000
        # A point lookup prunes to a single row group, so this is a handful of
        # ranged GETs rather than a full scan.
        assert scalar(f"SELECT id FROM read_parquet('{uri}') WHERE id = 31337") == 31337
        # glob drives ListObjectsV2 through httpfs, which the orphan scan uses.
        assert scalar(f"SELECT count(*) FROM glob('s3://{bucket}/{prefix}*.parquet')") == 1
    finally:
        con.close()
