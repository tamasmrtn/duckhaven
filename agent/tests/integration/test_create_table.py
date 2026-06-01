"""End-to-end Iceberg write/read through DuckDB + Polaris.

Exercises catalog-managed `INSERT` through DuckDB. With the `iceberg`
extension and a Polaris REST catalog, `CREATE TABLE` / `INSERT` / `SELECT`
all work against a catalog-managed table.

Opt-in (`-m integration`); requires a live Polaris that shares a
filesystem with this process (FILE storage). See conftest.
"""

from __future__ import annotations

import duckdb
import pytest

pytestmark = pytest.mark.integration


def _attach(conn: duckdb.DuckDBPyConnection, base_url: str, warehouse: str, creds) -> None:
    client_id, client_secret = creds
    conn.execute("INSTALL iceberg")
    conn.execute("LOAD iceberg")
    conn.execute(
        "CREATE SECRET dh_iceberg "
        "(TYPE ICEBERG, CLIENT_ID ?, CLIENT_SECRET ?, OAUTH2_SERVER_URI ?)",
        [client_id, client_secret, f"{base_url}/api/catalog/v1/oauth/tokens"],
    )
    conn.execute(
        "ATTACH ? AS dh_catalog (TYPE ICEBERG, SECRET dh_iceberg, "
        "ENDPOINT ?, ACCESS_DELEGATION_MODE 'none')",
        [warehouse, f"{base_url}/api/catalog"],
    )
    conn.execute("USE dh_catalog")


async def test_create_insert_select_roundtrip(
    polaris_base_url: str, polaris_creds, polaris_catalog: str
) -> None:
    conn = duckdb.connect()
    try:
        _attach(conn, polaris_base_url, polaris_catalog, polaris_creds)
        conn.execute("CREATE TABLE main.events (id BIGINT, label VARCHAR)")
        conn.execute("INSERT INTO main.events VALUES (1, 'one'), (2, 'two')")
        rows = conn.execute("SELECT id, label FROM main.events ORDER BY id").fetchall()
    finally:
        conn.close()
    assert rows == [(1, "one"), (2, "two")]
