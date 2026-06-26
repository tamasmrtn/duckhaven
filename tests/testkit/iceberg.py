"""DuckDB ↔ Polaris attach helper for integration tests.

Mirrors the agent runner's ``_attach_polaris`` (an Iceberg OAuth2 SECRET +
``ATTACH … (TYPE ICEBERG …)`` + ``USE <cat>.<ns>``), with Polaris vending
scoped object-store credentials to DuckDB. Lives in testkit (separate module
from ``polaris`` so importing the httpx provisioning helpers doesn't pull in
DuckDB) and is shared by the agent integration suite and the cross-component
harness instead of being duplicated per suite.
"""

from __future__ import annotations

import duckdb


def attach_catalog(
    conn: duckdb.DuckDBPyConnection,
    base_url: str,
    catalog: str,
    namespace: str,
    creds: tuple[str, str],
    *,
    delegation: str = "vended_credentials",
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
    wh = catalog.replace("'", "''")
    endpoint = f"{base_url}/api/catalog".replace("'", "''")
    conn.execute(
        f"ATTACH '{wh}' AS dh_catalog (TYPE ICEBERG, SECRET dh_iceberg, "
        f"ENDPOINT '{endpoint}', ACCESS_DELEGATION_MODE '{delegation}')"
    )
    conn.execute(f'USE dh_catalog."{namespace}"')


def attach_catalogs(
    conn: duckdb.DuckDBPyConnection,
    base_url: str,
    catalogs: list[tuple[str, str]],
    active: str,
    namespace: str,
    creds: tuple[str, str],
    *,
    delegation: str = "vended_credentials",
) -> None:
    """Multi-attach: ATTACH every ``(alias, polaris_name)`` under its alias and
    ``USE`` the active one — mirrors the agent runner's ``_attach_catalogs`` so
    cross-catalog ``alias.schema.table`` joins resolve in integration tests."""
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
    endpoint = f"{base_url}/api/catalog".replace("'", "''")
    for alias, polaris_name in catalogs:
        wh = polaris_name.replace("'", "''")
        a = alias.replace('"', '""')
        conn.execute(
            f"ATTACH '{wh}' AS \"{a}\" (TYPE ICEBERG, SECRET dh_iceberg, "
            f"ENDPOINT '{endpoint}', ACCESS_DELEGATION_MODE '{delegation}')"
        )
    conn.execute(f'USE "{active}"."{namespace}"')
