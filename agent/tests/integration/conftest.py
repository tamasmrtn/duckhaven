"""Shared fixtures for agent integration tests.

Drives Apache Polaris directly over REST (no dependency on the api
package) via the repo-root ``dh_testkit.polaris`` helpers, so the agent's
DuckDB path can be exercised end-to-end. Skipped when POLARIS_BASE_URL is
unset or the server is unreachable.

Polaris is object-storage only (see ADR 0001). The `polaris_s3_catalog`
fixture creates an S3-backed catalog and requires POLARIS_S3_BUCKET (+
POLARIS_S3_ENDPOINT[_INTERNAL]). It supports both reads and `INSERT`, since
Polaris vends scoped object-store credentials to DuckDB. `make polaris-dev`
provides a local MinIO-backed stack.
"""

from __future__ import annotations

import os
import sys
from collections.abc import AsyncIterator, Callable, Iterator
from pathlib import Path

import duckdb
import httpx
import pytest

# Make the repo-root `dh_testkit` package importable from this per-package run.
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from dh_testkit import polaris as dh_polaris  # noqa: E402


@pytest.fixture(scope="session")
def polaris_base_url() -> str:
    url = os.getenv("POLARIS_BASE_URL")
    if not url:
        pytest.skip("POLARIS_BASE_URL not set; skipping agent Polaris integration test")
    try:
        httpx.get(dh_polaris.health_url(url), timeout=2.0).raise_for_status()
    except httpx.HTTPError as exc:
        pytest.skip(f"Polaris unreachable at {url}: {exc}")
    return url


@pytest.fixture(scope="session")
def polaris_creds() -> tuple[str, str]:
    return dh_polaris.env_creds()


@pytest.fixture
async def polaris_s3_catalog(
    polaris_base_url: str, polaris_creds: tuple[str, str]
) -> AsyncIterator[tuple[str, str]]:
    """S3-backed catalog (object storage supports writes via vended creds).
    Requires POLARIS_S3_BUCKET (+ POLARIS_S3_ENDPOINT[_INTERNAL])."""
    if not os.getenv("POLARIS_S3_BUCKET"):
        pytest.skip("POLARIS_S3_BUCKET not set; skipping S3 write integration test")
    async with dh_polaris.s3_catalog(polaris_base_url, polaris_creds, prefix="dh_agt") as cat:
        yield cat


def _attach_catalog(
    conn: duckdb.DuckDBPyConnection,
    base_url: str,
    catalog: str,
    namespace: str,
    creds: tuple[str, str],
    *,
    delegation: str = "vended_credentials",
) -> None:
    """Mirror the agent runner's attach (`runner._attach_polaris`): an iceberg
    OAuth2 SECRET + `ATTACH … (TYPE ICEBERG …)` + `USE <cat>.<ns>`, with Polaris
    vending scoped object-store credentials to DuckDB."""
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


@pytest.fixture
def attach_factory(
    polaris_base_url: str, polaris_creds: tuple[str, str]
) -> Iterator[Callable[..., duckdb.DuckDBPyConnection]]:
    """Return ``make(catalog, namespace, *, delegation=...)`` -> an attached
    DuckDB connection. Connections are closed on teardown. Sync (DuckDB is sync)
    and depends only on sync fixtures, so async tests can pass the catalog tuple
    resolved from ``polaris_s3_catalog``."""
    conns: list[duckdb.DuckDBPyConnection] = []

    def _make(
        catalog: str, namespace: str, *, delegation: str = "vended_credentials"
    ) -> duckdb.DuckDBPyConnection:
        conn = duckdb.connect()
        _attach_catalog(
            conn, polaris_base_url, catalog, namespace, polaris_creds, delegation=delegation
        )
        conns.append(conn)
        return conn

    yield _make
    for conn in conns:
        conn.close()
