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
    creds from Polaris and can write + read object-store-backed Iceberg data."""
    catalog, ns = polaris_s3_catalog
    conn = attach_factory(catalog, ns, delegation="vended_credentials")
    conn.execute("INSERT INTO events VALUES (1, 'vended')")
    assert conn.execute("SELECT label FROM events WHERE id = 1").fetchone() == ("vended",)


async def test_external_s3_vended_credentials_enable_write(
    external_s3_catalog, attach_factory
) -> None:
    """An external assume-role S3 catalog reads and writes through the same
    vended-credentials path as the bundled object store: Polaris assumes the
    role server-side and hands DuckDB scoped creds. Gated on DH_TEST_S3_*."""
    catalog, ns = external_s3_catalog
    conn = attach_factory(catalog, ns, delegation="vended_credentials")
    conn.execute("INSERT INTO events VALUES (7, 'external')")
    assert conn.execute("SELECT label FROM events WHERE id = 7").fetchone() == ("external",)


async def test_without_delegation_object_store_is_unreadable(
    polaris_s3_catalog, attach_factory
) -> None:
    """With delegation 'none' Polaris vends nothing and the agent holds no S3
    secret, so reading object-store-backed data fails rather than silently returning
    wrong/empty results."""
    catalog, ns = polaris_s3_catalog
    with pytest.raises(duckdb.Error):
        conn = attach_factory(catalog, ns, delegation="none")
        # The read forces metadata/data fetches from the store without credentials.
        conn.execute("INSERT INTO events VALUES (1, 'x')")
        conn.execute("SELECT * FROM events").fetchall()


async def test_invalid_polaris_credentials_fail_attach(
    polaris_base_url, polaris_s3_catalog
) -> None:
    """Bad Polaris client credentials fail the OAuth2 client-credentials
    exchange — the agent cannot impersonate a principal it cannot authenticate
    as. DuckDB performs the exchange eagerly at CREATE SECRET time, so the
    failure surfaces there (or, for engines that defer it, at ATTACH)."""
    catalog, ns = polaris_s3_catalog
    conn = duckdb.connect()
    try:
        conn.execute("INSTALL iceberg")
        conn.execute("LOAD iceberg")
        conn.execute("INSTALL httpfs")
        conn.execute("LOAD httpfs")
        endpoint = f"{polaris_base_url}/api/catalog"
        with pytest.raises(duckdb.Error):
            conn.execute(
                "CREATE SECRET dh_iceberg "
                "(TYPE ICEBERG, CLIENT_ID ?, CLIENT_SECRET ?, OAUTH2_SERVER_URI ?)",
                ["root", "wrong-secret", f"{polaris_base_url}/api/catalog/v1/oauth/tokens"],
            )
            conn.execute(
                f"ATTACH '{catalog}' AS dh_catalog (TYPE ICEBERG, SECRET dh_iceberg, "
                f"ENDPOINT '{endpoint}', ACCESS_DELEGATION_MODE 'vended_credentials')"
            )
            conn.execute(f'USE dh_catalog."{ns}"')
            conn.execute("SELECT * FROM events").fetchall()
    finally:
        conn.close()


@pytest.mark.parametrize("with_fallback", [False, True])
async def test_the_fallback_secret_covers_a_replaced_vended_secret(
    polaris_s3_catalog, polaris_base_url, polaris_creds, with_fallback
) -> None:
    """The Iceberg extension replaces a table's vended secret on every bind of the
    table, so a statement that reads it twice has a moment with no secret. A read
    landing there went to the default S3 endpoint and, on a DuckDB background
    thread, aborted the agent (REPORT_RESULT_CACHE_V2.md, finding 1).

    Recreated deterministically: drop the vended secret, then read a data file.
    Without the catalog's fallback secret that is the crash's own error; with it,
    the read succeeds. Runs the production attach path end to end.
    """
    import os
    from urllib.parse import urlparse

    from agent.executor import runner

    catalog, ns = polaris_s3_catalog
    endpoint = urlparse(os.environ.get("POLARIS_S3_ENDPOINT", "http://127.0.0.1:9000"))
    descriptor = {
        "slug": "lake",
        "kind": "iceberg_polaris",
        "polaris_name": catalog,
        "backend": {"kind": "object_store"},
        "default_schema": ns,
    }
    if with_fallback:
        descriptor["storage"] = {
            "type": "s3",
            "scope": os.environ["POLARIS_S3_BUCKET"].rstrip("/") + "/",
            "key_id": os.getenv("OBJECT_STORE_ACCESS_KEY", "duckhaven"),
            "secret": os.getenv("OBJECT_STORE_SECRET_KEY", "duckhaven"),
            "region": os.getenv("POLARIS_S3_REGION", "us-east-1"),
            "endpoint": endpoint.netloc,
            "url_style": "path",
            "use_ssl": endpoint.scheme == "https",
        }
    polaris = {
        "endpoint": polaris_base_url,
        "client_id": polaris_creds[0],
        "client_secret": polaris_creds[1],
    }
    conn = runner.open_and_attach(
        catalogs=[descriptor], active_catalog="lake", polaris=polaris, lock_config=True
    )
    try:
        conn.execute(f"INSERT INTO lake.\"{ns}\".events VALUES (1, 'fallback')")
        conn.execute("BEGIN")
        path = conn.execute(
            f'SELECT file_path FROM iceberg_metadata(lake."{ns}".events) '
            "WHERE manifest_content = 'DATA' LIMIT 1"
        ).fetchone()[0]
        vended = conn.execute(
            "SELECT name FROM duckdb_secrets() WHERE name LIKE '__internal_ic_%'"
        ).fetchall()
        assert vended, "no vended secret to remove: the test would prove nothing"
        for (name,) in vended:
            conn.execute(f'DROP TEMPORARY SECRET "{name}"')

        read = f"SELECT count(*) FROM read_parquet('{path}')"
        if with_fallback:
            assert conn.execute(read).fetchone()[0] >= 1
        else:
            with pytest.raises(duckdb.Error):
                conn.execute(read).fetchone()
    finally:
        conn.close()
