"""Agent DDL/DML execution against a live Polaris (Iceberg REST) catalog.

Opt-in (`-m integration`); requires a live Polaris on object storage (see
conftest; `make polaris-dev` provides a local object-store-backed stack). Drives the
real runner (`run_query_sync`) — the same code the control plane dispatches to —
so it validates both the SELECT-vs-side-effecting branch and that DuckDB's
`iceberg` extension actually executes CREATE / ALTER / DROP against the REST
catalog (the risk the SQL-DDL feature hinges on).

Each call opens its own connection (as a dispatched query does), so a table
created in one call is visible to the next because Polaris persists it.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from uuid import uuid4

import duckdb
import pytest

from agent.executor.runner import run_query_sync

pytestmark = pytest.mark.integration


def _run(
    sql: str,
    *,
    base_url: str,
    creds: tuple[str, str],
    catalog: str,
    ns: str,
    tmp_path: Path,
) -> dict:
    """Execute one statement through the real runner with the catalog attached."""
    return run_query_sync(
        sql,
        tmp_path / f"{uuid4().hex}.parquet",
        memory_bytes=1024**3,
        threads=2,
        catalogs=[
            {
                "slug": catalog,
                "polaris_name": catalog,
                "backend": {"kind": "s3"},
                "default_schema": ns,
            }
        ],
        active_catalog=catalog,
        polaris={"endpoint": base_url, "client_id": creds[0], "client_secret": creds[1]},
    )


async def test_create_insert_select_drop_roundtrip(
    polaris_base_url: str, polaris_creds, polaris_s3_catalog: tuple[str, str], tmp_path: Path
) -> None:
    catalog, ns = polaris_s3_catalog
    table = f"dh_ddl_{uuid4().hex[:8]}"

    def run(sql: str) -> dict:
        return _run(
            sql,
            base_url=polaris_base_url,
            creds=polaris_creds,
            catalog=catalog,
            ns=ns,
            tmp_path=tmp_path,
        )

    # CREATE: runs directly, writes no result file.
    created = run(f"CREATE TABLE {table} (id INTEGER, label VARCHAR)")
    assert created["wrote_result"] is False
    assert created["row_count"] == 0

    # INSERT: reports the affected-row count, still no result file.
    inserted = run(f"INSERT INTO {table} VALUES (1, 'one'), (2, 'two')")
    assert inserted["wrote_result"] is False
    assert inserted["row_count"] == 2

    # SELECT: materialized to Parquet.
    result_path = tmp_path / "sel.parquet"
    selected = run_query_sync(
        f"SELECT id, label FROM {table} ORDER BY id",
        result_path,
        memory_bytes=1024**3,
        threads=2,
        catalogs=[
            {
                "slug": catalog,
                "polaris_name": catalog,
                "backend": {"kind": "s3"},
                "default_schema": ns,
            }
        ],
        active_catalog=catalog,
        polaris={
            "endpoint": polaris_base_url,
            "client_id": polaris_creds[0],
            "client_secret": polaris_creds[1],
        },
    )
    assert selected["wrote_result"] is True
    assert selected["row_count"] == 2
    assert result_path.exists()
    assert duckdb.read_parquet(str(result_path)).fetchall() == [(1, "one"), (2, "two")]

    # DROP: runs directly; the table is then gone.
    dropped = run(f"DROP TABLE {table}")
    assert dropped["wrote_result"] is False
    with pytest.raises(Exception):  # noqa: B017 - any resolution error proves it's gone
        run(f"SELECT * FROM {table}")


async def test_alter_table_add_column(
    polaris_base_url: str, polaris_creds, polaris_s3_catalog: tuple[str, str], tmp_path: Path
) -> None:
    catalog, ns = polaris_s3_catalog
    table = f"dh_alt_{uuid4().hex[:8]}"

    def run(sql: str) -> dict:
        return _run(
            sql,
            base_url=polaris_base_url,
            creds=polaris_creds,
            catalog=catalog,
            ns=ns,
            tmp_path=tmp_path,
        )

    run(f"CREATE TABLE {table} (id INTEGER)")

    altered = run(f"ALTER TABLE {table} ADD COLUMN note VARCHAR")
    assert altered["wrote_result"] is False

    # The new column is usable: insert + read it back proves the ALTER committed.
    run(f"INSERT INTO {table} VALUES (1, 'hello')")
    result_path = tmp_path / "alt.parquet"
    selected = run_query_sync(
        f"SELECT note FROM {table}",
        result_path,
        memory_bytes=1024**3,
        threads=2,
        catalogs=[
            {
                "slug": catalog,
                "polaris_name": catalog,
                "backend": {"kind": "s3"},
                "default_schema": ns,
            }
        ],
        active_catalog=catalog,
        polaris={
            "endpoint": polaris_base_url,
            "client_id": polaris_creds[0],
            "client_secret": polaris_creds[1],
        },
    )
    assert selected["wrote_result"] is True
    assert duckdb.read_parquet(str(result_path)).fetchall() == [("hello",)]

    run(f"DROP TABLE {table}")


def _object_store_keys(prefix_uri: str) -> list[str]:
    """Keys under an s3:// prefix on the bundled store."""
    import boto3
    from botocore.config import Config

    endpoint = os.getenv("POLARIS_S3_ENDPOINT") or os.getenv("POLARIS_S3_ENDPOINT_INTERNAL")
    bucket, _, prefix = prefix_uri.removeprefix("s3://").partition("/")
    client = boto3.client(
        "s3",
        endpoint_url=endpoint,
        region_name=os.getenv("POLARIS_S3_REGION", "us-east-1"),
        aws_access_key_id=os.getenv("OBJECT_STORE_ACCESS_KEY", "duckhaven"),
        aws_secret_access_key=os.getenv("OBJECT_STORE_SECRET_KEY", "duckhaven"),
        config=Config(s3={"addressing_style": "path"}, signature_version="s3v4"),
    )
    return [
        o["Key"] for o in client.list_objects_v2(Bucket=bucket, Prefix=prefix).get("Contents", [])
    ]


async def test_drop_table_purges_its_files(
    polaris_base_url: str, polaris_creds, polaris_s3_catalog: tuple[str, str], tmp_path: Path
) -> None:
    """A SQL DROP TABLE reclaims the table's data and metadata files.

    Dropping with purgeRequested=false (DuckDB's default) removes the catalog
    entry and leaves every file behind; the old roundtrip test could not see
    that because it only checked the table stopped resolving.
    """
    catalog, ns = polaris_s3_catalog
    table = f"dh_purge_{uuid4().hex[:8]}"

    def run(sql: str, out: Path | None = None) -> dict:
        return run_query_sync(
            sql,
            out or tmp_path / "unused.parquet",
            memory_bytes=1024**3,
            threads=2,
            catalogs=[
                {
                    "slug": catalog,
                    "polaris_name": catalog,
                    "backend": {"kind": "s3"},
                    "default_schema": ns,
                }
            ],
            active_catalog=catalog,
            polaris={
                "endpoint": polaris_base_url,
                "client_id": polaris_creds[0],
                "client_secret": polaris_creds[1],
            },
        )

    run(f"CREATE TABLE {table} AS SELECT range AS id FROM range(1000)")
    files_path = tmp_path / "files.parquet"
    run(f'SELECT file_path FROM iceberg_metadata("{catalog}"."{ns}"."{table}")', files_path)
    data_file = duckdb.read_parquet(str(files_path)).fetchone()[0]
    table_prefix = data_file.split("/data/")[0] + "/"
    assert _object_store_keys(table_prefix), "the table should have files before the drop"

    run(f"DROP TABLE {table}")

    # Polaris purges in a background task after acknowledging the drop.
    deadline = time.monotonic() + 30
    while (left := _object_store_keys(table_prefix)) and time.monotonic() < deadline:
        time.sleep(1)
    assert left == []
