"""Polaris credential vending + scoping failure modes.

The agent never holds storage keys: it presents Polaris client credentials and
Polaris vends scoped, short-lived object-store credentials to DuckDB. These
tests confirm vending actually gates object-store access — both the success
path and the failures when delegation is off or the Polaris credentials are bad.
"""

from __future__ import annotations

import duckdb
import pytest

pytestmark = pytest.mark.integration


async def test_vended_credentials_enable_object_store_write(
    polaris_s3_catalog, attach_factory
) -> None:
    """With ACCESS_DELEGATION_MODE 'vended_credentials', DuckDB receives scoped
    creds from Polaris and can write + read MinIO-backed Iceberg data."""
    catalog, ns = polaris_s3_catalog
    conn = attach_factory(catalog, ns, delegation="vended_credentials")
    conn.execute("INSERT INTO events VALUES (1, 'vended')")
    assert conn.execute("SELECT label FROM events WHERE id = 1").fetchone() == ("vended",)


async def test_without_delegation_object_store_is_unreadable(
    polaris_s3_catalog, attach_factory
) -> None:
    """With delegation 'none' Polaris vends nothing and the agent holds no S3
    secret, so reading MinIO-backed data fails rather than silently returning
    wrong/empty results."""
    catalog, ns = polaris_s3_catalog
    with pytest.raises(duckdb.Error):
        conn = attach_factory(catalog, ns, delegation="none")
        # The read forces metadata/data fetches from MinIO without credentials.
        conn.execute("INSERT INTO events VALUES (1, 'x')")
        conn.execute("SELECT * FROM events").fetchall()


async def test_invalid_polaris_credentials_fail_attach(
    polaris_base_url, polaris_s3_catalog
) -> None:
    """Bad Polaris client credentials fail the OAuth2 exchange at ATTACH time —
    the agent cannot impersonate a principal it cannot authenticate as."""
    catalog, ns = polaris_s3_catalog
    conn = duckdb.connect()
    try:
        conn.execute("INSTALL iceberg")
        conn.execute("LOAD iceberg")
        conn.execute("INSTALL httpfs")
        conn.execute("LOAD httpfs")
        conn.execute(
            "CREATE SECRET dh_iceberg "
            "(TYPE ICEBERG, CLIENT_ID ?, CLIENT_SECRET ?, OAUTH2_SERVER_URI ?)",
            ["root", "wrong-secret", f"{polaris_base_url}/api/catalog/v1/oauth/tokens"],
        )
        endpoint = f"{polaris_base_url}/api/catalog"
        with pytest.raises(duckdb.Error):
            conn.execute(
                f"ATTACH '{catalog}' AS dh_catalog (TYPE ICEBERG, SECRET dh_iceberg, "
                f"ENDPOINT '{endpoint}', ACCESS_DELEGATION_MODE 'vended_credentials')"
            )
            conn.execute(f'USE dh_catalog."{ns}"')
            conn.execute("SELECT * FROM events").fetchall()
    finally:
        conn.close()
