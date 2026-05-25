"""Spike S3 — Create UC-managed Delta tables on each backend kind.

What this spike has to answer:
1. Does UC OSS 0.4 accept `delta.feature.catalogManaged=supported` on
   `POST /tables`?
2. After agent INSERTs a row through DuckDB's `unity_catalog` +
   `delta` extensions, does the Delta log record that feature flag?
3. Does the same flow work against S3 / ADLS Gen2 (gated on env)?

Findings here shape Step 10 (agent runner.py): if UC accepts the
property on create, we're done. If it doesn't, the agent must
`ALTER TABLE ... SET TBLPROPERTIES (...)` after the first write.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import duckdb
import httpx
import pytest

pytestmark = pytest.mark.integration


CATALOG_MANAGED_KEY = "delta.feature.catalogManaged"


def read_delta_log_features(table_root: Path) -> set[str]:
    """Return the set of `delta.feature.*` keys recorded in the table's log."""

    log_dir = table_root / "_delta_log"
    features: set[str] = set()
    if not log_dir.exists():
        return features
    for entry in sorted(log_dir.glob("*.json")):
        for line in entry.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            action = json.loads(line)
            meta = action.get("metaData")
            if meta:
                for key in meta.get("configuration") or {}:
                    if key.startswith("delta.feature."):
                        features.add(key)
    return features


async def _create_managed_delta_table(
    uc_http: httpx.AsyncClient,
    catalog: str,
    schema: str,
    name: str,
    storage_location: str,
) -> dict:
    """POST a Delta-format managed table to UC and return the response body."""

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
            },
            {
                "name": "label",
                "type_text": "string",
                "type_json": '{"name":"label","type":"string","nullable":true,"metadata":{}}',
                "type_name": "STRING",
                "type_precision": 0,
                "type_scale": 0,
                "type_interval_type": None,
                "position": 1,
                "nullable": True,
                "comment": None,
            },
        ],
        "properties": {CATALOG_MANAGED_KEY: "supported"},
        "comment": "duckhaven spike S3",
    }
    resp = await uc_http.post("/api/2.1/unity-catalog/tables", json=body)
    resp.raise_for_status()
    return resp.json()


def _attach_uc_and_insert(
    conn: duckdb.DuckDBPyConnection,
    uc_base_url: str,
    catalog: str,
    schema: str,
    table: str,
) -> list[tuple]:
    """Load extensions, ATTACH the UC catalog, INSERT a row, return SELECT *."""

    for ext in ("delta", "unity_catalog"):
        conn.execute(f"INSTALL {ext}")
        conn.execute(f"LOAD {ext}")
    conn.execute(f"ATTACH '{catalog}' AS uc_attached (TYPE UC_CATALOG, ENDPOINT '{uc_base_url}')")
    conn.execute(f"INSERT INTO uc_attached.{schema}.{table} VALUES (1, 'one'), (2, 'two')")
    rows = conn.execute(
        f"SELECT id, label FROM uc_attached.{schema}.{table} ORDER BY id"
    ).fetchall()
    return rows


@pytest.mark.skip(
    reason=(
        "UC OSS 0.4.0 does not implement /api/2.1/unity-catalog/delta/preview/commits, "
        "and the DuckDB unity_catalog extension (v0202409) calls that endpoint on every "
        "INSERT against a catalogManaged Delta table. It also expects an existing "
        "_delta_log under the table's storage_location, which UC OSS does not "
        "bootstrap. Re-enable once UC OSS ships the coordinated-commits API "
        "(tracked upstream) or once the agent bootstraps the Delta log itself."
    )
)
async def test_create_table_local_fs(
    uc_http: httpx.AsyncClient,
    uc_base_url: str,
    uc_catalog: str,
    uc_schema: str,
    backend_root: Path,
) -> None:
    """Happy path on a local-fs backend: create, attach, insert, select,
    and confirm the catalogManaged feature flag is recorded in the log."""

    table_name = "events"
    table_root = backend_root / uc_schema / table_name
    table_root.mkdir(parents=True)
    storage_location = table_root.as_uri()

    created = await _create_managed_delta_table(
        uc_http, uc_catalog, uc_schema, table_name, storage_location
    )
    assert (created.get("properties") or {}).get(CATALOG_MANAGED_KEY) == "supported"

    conn = duckdb.connect()
    try:
        rows = _attach_uc_and_insert(conn, uc_base_url, uc_catalog, uc_schema, table_name)
    finally:
        conn.close()

    assert rows == [(1, "one"), (2, "two")]

    features = read_delta_log_features(table_root)
    assert "delta.feature.catalogManaged" in features, (
        f"Expected catalogManaged feature flag in Delta log; got {features}"
    )


@pytest.mark.skipif(
    not os.getenv("M3_S3_BUCKET"),
    reason="M3_S3_BUCKET not set; skipping S3 backend spike",
)
async def test_create_table_s3(
    uc_http: httpx.AsyncClient,
    uc_base_url: str,
    uc_catalog: str,
    uc_schema: str,
) -> None:
    """Same path on an s3:// backend root. Requires real S3 creds in env
    (AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_REGION)."""

    bucket = os.environ["M3_S3_BUCKET"]
    table_name = "events_s3"
    storage_location = f"s3://{bucket}/duckhaven-spike/{uc_catalog}/{uc_schema}/{table_name}/"

    created = await _create_managed_delta_table(
        uc_http, uc_catalog, uc_schema, table_name, storage_location
    )
    assert (created.get("properties") or {}).get(CATALOG_MANAGED_KEY) == "supported"

    conn = duckdb.connect()
    try:
        conn.execute("INSTALL httpfs")
        conn.execute("LOAD httpfs")
        conn.execute(
            "CREATE SECRET ws_spike (TYPE S3, KEY_ID ?, SECRET ?, REGION ?)",
            [
                os.environ["AWS_ACCESS_KEY_ID"],
                os.environ["AWS_SECRET_ACCESS_KEY"],
                os.environ.get("AWS_REGION", "us-east-1"),
            ],
        )
        rows = _attach_uc_and_insert(conn, uc_base_url, uc_catalog, uc_schema, table_name)
    finally:
        conn.close()

    assert rows == [(1, "one"), (2, "two")]


@pytest.mark.skipif(
    not (
        os.getenv("M3_ADLS_ACCOUNT")
        and os.getenv("M3_ADLS_CONTAINER")
        and os.getenv("M3_ADLS_CONNECTION_STRING")
    ),
    reason="M3_ADLS_* not fully set; skipping ADLS Gen2 backend spike",
)
async def test_create_table_adls(
    uc_http: httpx.AsyncClient,
    uc_base_url: str,
    uc_catalog: str,
    uc_schema: str,
) -> None:
    """Same path on an abfss:// backend root. Requires real ADLS creds."""

    account = os.environ["M3_ADLS_ACCOUNT"]
    container = os.environ["M3_ADLS_CONTAINER"]
    table_name = "events_adls"
    storage_location = (
        f"abfss://{container}@{account}.dfs.core.windows.net/"
        f"duckhaven-spike/{uc_catalog}/{uc_schema}/{table_name}/"
    )

    created = await _create_managed_delta_table(
        uc_http, uc_catalog, uc_schema, table_name, storage_location
    )
    assert (created.get("properties") or {}).get(CATALOG_MANAGED_KEY) == "supported"

    conn = duckdb.connect()
    try:
        conn.execute("INSTALL azure")
        conn.execute("LOAD azure")
        conn.execute(
            "CREATE SECRET ws_spike (TYPE AZURE, CONNECTION_STRING ?)",
            [os.environ["M3_ADLS_CONNECTION_STRING"]],
        )
        rows = _attach_uc_and_insert(conn, uc_base_url, uc_catalog, uc_schema, table_name)
    finally:
        conn.close()

    assert rows == [(1, "one"), (2, "two")]
