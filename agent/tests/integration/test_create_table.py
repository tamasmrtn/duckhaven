"""Agent DuckDB ↔ Polaris read/attach + write path over the Iceberg REST catalog.

Opt-in (`-m integration`); requires a live Polaris on object storage (see
conftest; `make polaris-dev` provides a local MinIO-backed stack). Validates
attaching the catalog, resolving the (non-`main`) namespace, loading a
REST-created table's schema, reading it, and the full INSERT roundtrip.

This mirrors the agent runner's attach pattern (`runner._attach_polaris`):
an iceberg OAuth2 SECRET + `ATTACH … (TYPE ICEBERG …)` + `USE <cat>.<ns>`,
with Polaris vending scoped object-store credentials to DuckDB.
"""

from __future__ import annotations

import duckdb
import pytest

pytestmark = pytest.mark.integration


def _attach(
    conn: duckdb.DuckDBPyConnection,
    base_url: str,
    warehouse: str,
    ns: str,
    creds,
    *,
    delegation: str = "none",
) -> None:
    client_id, client_secret = creds
    conn.execute("INSTALL iceberg")
    conn.execute("LOAD iceberg")
    conn.execute("INSTALL httpfs")
    conn.execute("LOAD httpfs")
    conn.execute(
        "CREATE SECRET dh_iceberg "
        "(TYPE ICEBERG, CLIENT_ID ?, CLIENT_SECRET ?, OAUTH2_SERVER_URI ?)",
        [client_id, client_secret, f"{base_url}/api/catalog/v1/oauth/tokens"],
    )
    # ATTACH does not accept bind parameters; inline the (trusted) values.
    wh = warehouse.replace("'", "''")
    endpoint = f"{base_url}/api/catalog".replace("'", "''")
    conn.execute(
        f"ATTACH '{wh}' AS dh_catalog (TYPE ICEBERG, SECRET dh_iceberg, "
        f"ENDPOINT '{endpoint}', ACCESS_DELEGATION_MODE '{delegation}')"
    )
    conn.execute(f'USE dh_catalog."{ns}"')


async def test_attach_and_read_rest_table(
    polaris_base_url: str, polaris_creds, polaris_s3_catalog: tuple[str, str]
) -> None:
    catalog, ns = polaris_s3_catalog
    conn = duckdb.connect()
    try:
        _attach(conn, polaris_base_url, catalog, ns, polaris_creds, delegation="vended_credentials")
        # The namespace resolves and the REST-created table's schema loads
        # (this is exactly what failed before the namespace/RBAC/USE fixes).
        cur = conn.execute("SELECT id, label FROM events")
        rows = cur.fetchall()
        columns = [d[0] for d in cur.description]
    finally:
        conn.close()
    assert rows == []  # freshly created, empty
    assert columns == ["id", "label"]


async def test_insert_select_roundtrip(
    polaris_base_url: str, polaris_creds, polaris_s3_catalog: tuple[str, str]
) -> None:
    """Full write path: INSERT then read it back. Requires object storage
    (S3), where Polaris vends scoped credentials to DuckDB — the capability the
    old Delta+UC stack could not do."""
    catalog, ns = polaris_s3_catalog
    conn = duckdb.connect()
    try:
        _attach(conn, polaris_base_url, catalog, ns, polaris_creds, delegation="vended_credentials")
        conn.execute("INSERT INTO events VALUES (1, 'one'), (2, 'two')")
        rows = conn.execute("SELECT id, label FROM events ORDER BY id").fetchall()
    finally:
        conn.close()
    assert rows == [(1, "one"), (2, "two")]
