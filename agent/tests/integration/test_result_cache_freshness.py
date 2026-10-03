"""What the result cache's admission check assumes about a held connection.

The control plane admits a result only if every table it read has the same
version before dispatch and after completion. That is sound only if the agent's
connection reads the catalog's *current* version when the statement runs. A
one-shot query opens a fresh connection, so it always does; a SQL session holds
one connection for many statements, so this suite pins down what such a
connection sees after another writer commits:

- an autocommit statement sees the commit, for both catalog kinds, so a session
  statement outside a transaction can be cached exactly like a one-shot query;
- inside an explicit transaction the first read pins the table, so a later
  statement in the same transaction can read an older version than the catalog
  reports -- which is why sessions never use the cache inside one.

If a DuckDB or extension upgrade changes either behaviour, these fail first.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from urllib.parse import urlparse

import duckdb
import pytest

from agent.executor import runner

pytestmark = pytest.mark.integration


def _iceberg_conn(base_url: str, creds: tuple[str, str], catalog: str, ns: str):
    conn = duckdb.connect()
    conn.execute("INSTALL httpfs")
    conn.execute("LOAD httpfs")
    runner._attach_catalogs(
        conn,
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
    return conn


async def test_iceberg_held_connection_sees_external_commit_between_statements(
    polaris_base_url: str, polaris_creds, polaris_s3_catalog: tuple[str, str]
) -> None:
    catalog, ns = polaris_s3_catalog
    held = _iceberg_conn(polaris_base_url, polaris_creds, catalog, ns)
    writer = _iceberg_conn(polaris_base_url, polaris_creds, catalog, ns)
    try:
        assert held.execute("SELECT count(*) FROM events").fetchone() == (0,)
        writer.execute("INSERT INTO events VALUES (1, 'a')")
        assert held.execute("SELECT count(*) FROM events").fetchone() == (1,)
    finally:
        held.close()
        writer.close()


async def test_iceberg_explicit_transaction_pins_the_first_read(
    polaris_base_url: str, polaris_creds, polaris_s3_catalog: tuple[str, str]
) -> None:
    catalog, ns = polaris_s3_catalog
    held = _iceberg_conn(polaris_base_url, polaris_creds, catalog, ns)
    writer = _iceberg_conn(polaris_base_url, polaris_creds, catalog, ns)
    try:
        held.execute("BEGIN")
        assert held.execute("SELECT count(*) FROM events").fetchone() == (0,)
        writer.execute("INSERT INTO events VALUES (1, 'a')")
        # Stale inside the transaction: the catalog already reports the insert.
        assert held.execute("SELECT count(*) FROM events").fetchone() == (0,)
        held.execute("COMMIT")
        assert held.execute("SELECT count(*) FROM events").fetchone() == (1,)
    finally:
        held.close()
        writer.close()


async def test_iceberg_alter_changes_the_schema_without_a_snapshot(
    polaris_base_url: str, polaris_creds, polaris_s3_catalog: tuple[str, str]
) -> None:
    """Why the cache keys on the schema id as well as the snapshot id."""
    catalog, ns = polaris_s3_catalog
    held = _iceberg_conn(polaris_base_url, polaris_creds, catalog, ns)
    writer = _iceberg_conn(polaris_base_url, polaris_creds, catalog, ns)
    ident = f'"{catalog}"."{ns}"."events"'
    try:
        writer.execute("INSERT INTO events VALUES (1, 'a')")
        snapshots = held.execute(f"SELECT count(*) FROM iceberg_snapshots({ident})").fetchone()
        writer.execute("ALTER TABLE events ADD COLUMN extra INTEGER")
        assert held.execute(f"SELECT count(*) FROM iceberg_snapshots({ident})").fetchone() == (
            snapshots
        )
        columns = [d[0] for d in held.execute("SELECT * FROM events LIMIT 0").description]
        assert columns == ["id", "label", "extra"]
    finally:
        held.close()
        writer.close()


@pytest.fixture
def ducklake_attach(tmp_path: Path):
    """Two connections onto one Postgres-backed DuckLake catalog, as two agents
    (or a session and an external writer) would see it."""
    url = os.getenv("DUCKLAKE_DATABASE_URL")
    if not url:
        pytest.skip("DUCKLAKE_DATABASE_URL not set; skipping DuckLake freshness test")
    parsed = urlparse(url.replace("+asyncpg", ""))
    dsn = (
        f"dbname={parsed.path.lstrip('/')} host={parsed.hostname} port={parsed.port or 5432} "
        f"user={parsed.username} password={parsed.password}"
    )
    schema = f"rc_{uuid.uuid4().hex[:8]}"
    conns: list[duckdb.DuckDBPyConnection] = []

    def _make() -> duckdb.DuckDBPyConnection:
        conn = duckdb.connect()
        conn.execute("INSTALL ducklake")
        conn.execute("LOAD ducklake")
        conn.execute("INSTALL postgres")
        conn.execute("LOAD postgres")
        conn.execute(
            f"ATTACH 'ducklake:postgres:{dsn}' AS lk "
            f"(DATA_PATH '{tmp_path}/', METADATA_SCHEMA '{schema}')"
        )
        conn.execute("USE lk")
        conns.append(conn)
        return conn

    yield _make
    for conn in conns:
        conn.close()
    cleanup = duckdb.connect()
    try:
        cleanup.execute("INSTALL postgres")
        cleanup.execute("LOAD postgres")
        cleanup.execute(f"ATTACH '{dsn}' AS pg (TYPE postgres)")
        cleanup.execute(f"CALL postgres_execute('pg', 'DROP SCHEMA IF EXISTS {schema} CASCADE')")
    finally:
        cleanup.close()


def test_ducklake_held_connection_sees_external_commit_between_statements(
    ducklake_attach,
) -> None:
    writer = ducklake_attach()
    writer.execute("CREATE TABLE t (a INTEGER)")
    held = ducklake_attach()
    assert held.execute("SELECT count(*) FROM t").fetchone() == (0,)
    writer.execute("INSERT INTO t VALUES (1), (2)")
    assert held.execute("SELECT count(*) FROM t").fetchone() == (2,)


def test_ducklake_explicit_transaction_pins_the_first_read(ducklake_attach) -> None:
    writer = ducklake_attach()
    writer.execute("CREATE TABLE t (a INTEGER)")
    held = ducklake_attach()
    held.execute("BEGIN")
    assert held.execute("SELECT count(*) FROM t").fetchone() == (0,)
    writer.execute("INSERT INTO t VALUES (1)")
    assert held.execute("SELECT count(*) FROM t").fetchone() == (0,)
    held.execute("COMMIT")
    assert held.execute("SELECT count(*) FROM t").fetchone() == (1,)
