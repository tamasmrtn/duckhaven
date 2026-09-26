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


def iceberg_secret_sql(client_id: str, client_secret: str, base_url: str) -> str:
    """The Iceberg OAuth2 secret, with its values inlined as escaped literals.

    Inlined because DuckDB 2.0 refuses bind parameters in ``CREATE SECRET``
    ("Unrecognized expression type PARAMETER"); every line accepts literals, and
    these are test credentials, so there is nothing to keep out of the statement.
    """

    def lit(value: str) -> str:
        return "'" + value.replace("'", "''") + "'"

    return (
        f"CREATE SECRET dh_iceberg (TYPE ICEBERG, CLIENT_ID {lit(client_id)}, "
        f"CLIENT_SECRET {lit(client_secret)}, "
        f"OAUTH2_SERVER_URI {lit(f'{base_url}/api/catalog/v1/oauth/tokens')})"
    )


def vended_credentials_are_secrets(conn: duckdb.DuckDBPyConnection) -> bool:
    """Whether the iceberg extension registered Polaris's vended credentials as a
    DuckDB secret, which is what lets a *direct* read of a table's files work.

    DuckDB 1.5 does, once a table has been touched. The DuckDB 2.0 pre-release
    keeps them inside the extension, so only Iceberg scans can use them, and the
    agent's footer size probe and orphan listing get a 403. Recorded as a blocker
    for promoting 2.0 in docs/developer/runtime-qualification.md.
    """
    return bool(
        conn.execute("SELECT count(*) FROM duckdb_secrets() WHERE provider = 'iceberg'").fetchone()[
            0
        ]
    )


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
    conn.execute(iceberg_secret_sql(client_id, client_secret, base_url))
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
    conn.execute(iceberg_secret_sql(client_id, client_secret, base_url))
    endpoint = f"{base_url}/api/catalog".replace("'", "''")
    for alias, polaris_name in catalogs:
        wh = polaris_name.replace("'", "''")
        a = alias.replace('"', '""')
        conn.execute(
            f"ATTACH '{wh}' AS \"{a}\" (TYPE ICEBERG, SECRET dh_iceberg, "
            f"ENDPOINT '{endpoint}', ACCESS_DELEGATION_MODE '{delegation}')"
        )
    conn.execute(f'USE "{active}"."{namespace}"')
