"""Table versions for the result cache, against a live Polaris and a live DuckLake
catalog database.

These pin the catalog behaviour the cache's correctness rests on: which commits
change a table's version, that a schema change is seen even when Iceberg makes
no snapshot for it, which DuckLake commits only rewrite files, and that an
unchanged Iceberg table costs a 304. Opt-in (`-m integration`).
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from urllib.parse import urlparse

import duckdb
import pytest
from testkit import polaris as dh_polaris
from testkit.iceberg import attach_catalog

from api.config import settings
from api.models.catalog import KIND_DUCKLAKE, Catalog
from api.services.catalog_backends import TableVersion, backend_for
from api.services.catalog_backends.ducklake import (
    dispose_engine,
    metadata_schema_for,
    new_role_password,
)
from api.services.catalog_backends.polaris import PolarisCatalogBackend
from api.services.polaris import PolarisClient

pytestmark = pytest.mark.integration


# --- Iceberg + Polaris ---------------------------------------------------------


@pytest.fixture
async def iceberg(polaris_base_url: str, polaris: PolarisClient):
    """A catalog with the seeded `analytics.events` table, a DuckDB writer on it,
    and the backend under test. Yields ``(backend, catalog_row, writer)``."""
    if not os.getenv("POLARIS_S3_BUCKET"):
        pytest.skip("POLARIS_S3_BUCKET not set")
    creds = dh_polaris.env_creds()
    async with dh_polaris.s3_catalog(polaris_base_url, creds, prefix="dh_rc") as (name, ns):
        writer = duckdb.connect()
        attach_catalog(writer, polaris_base_url, name, ns, creds)
        catalog = Catalog(slug=name, name=name, polaris_name=name, kind="iceberg_polaris")
        try:
            yield PolarisCatalogBackend(polaris), catalog, writer, ns
        finally:
            writer.close()


def _statuses(polaris: PolarisClient) -> list[int]:
    seen: list[int] = []

    async def hook(response) -> None:
        if response.request.method == "GET" and "/tables/" in str(response.request.url):
            seen.append(response.status_code)

    polaris._http.event_hooks["response"].append(hook)
    return seen


async def test_an_unchanged_table_is_answered_with_a_304(iceberg, polaris: PolarisClient) -> None:
    backend, catalog, writer, ns = iceberg
    writer.execute("INSERT INTO events VALUES (1, 'a')")
    statuses = _statuses(polaris)
    first = await backend.table_versions(catalog, [(ns, "events")])
    second = await backend.table_versions(catalog, [(ns, "events")])
    assert first == second
    assert statuses == [200, 304]


async def test_an_append_changes_both_strengths(iceberg) -> None:
    backend, catalog, writer, ns = iceberg
    writer.execute("INSERT INTO events VALUES (1, 'a')")
    before = (await backend.table_versions(catalog, [(ns, "events")]))[(ns, "events")]
    writer.execute("INSERT INTO events VALUES (2, 'b')")
    after = (await backend.table_versions(catalog, [(ns, "events")]))[(ns, "events")]
    assert after.content_id != before.content_id
    assert after.version_token != before.version_token
    assert not await backend.data_equivalent(catalog, ns, "events", before, after)


async def test_an_alter_changes_the_version_without_a_snapshot(iceberg) -> None:
    backend, catalog, writer, ns = iceberg
    writer.execute("INSERT INTO events VALUES (1, 'a')")
    before = (await backend.table_versions(catalog, [(ns, "events")]))[(ns, "events")]
    writer.execute("ALTER TABLE events ADD COLUMN extra INTEGER")
    after = (await backend.table_versions(catalog, [(ns, "events")]))[(ns, "events")]
    # Same snapshot, new schema: the content id still moves.
    assert before.content_id.split(":")[1] == after.content_id.split(":")[1]
    assert after.content_id != before.content_id
    assert after.version_token != before.version_token
    assert not await backend.data_equivalent(catalog, ns, "events", before, after)


async def test_a_missing_table_is_absent(iceberg) -> None:
    backend, catalog, _, ns = iceberg
    versions = await backend.table_versions(catalog, [(ns, "events"), (ns, "nope")])
    assert set(versions) == {(ns, "events")}


# --- DuckLake ----------------------------------------------------------------


@pytest.fixture
async def ducklake(tmp_path: Path):
    """A provisioned DuckLake catalog, a DuckDB writer attached to it, and the
    backend under test."""
    url = os.getenv("DUCKLAKE_DATABASE_URL")
    if not url:
        pytest.skip("DUCKLAKE_DATABASE_URL not set")
    original = (settings.ducklake_enabled, settings.ducklake_database_url)
    settings.ducklake_enabled = True
    settings.ducklake_database_url = url
    slug = f"rc_{uuid.uuid4().hex[:8]}"
    catalog = Catalog(
        slug=slug, name=slug, kind=KIND_DUCKLAKE, metadata_schema=metadata_schema_for(slug)
    )
    catalog.pending_ducklake_password = new_role_password()
    backend = backend_for(catalog)
    await backend.provision(catalog)
    parsed = urlparse(url.replace("+asyncpg", ""))
    dsn = (
        f"dbname={parsed.path.lstrip('/')} host={parsed.hostname} port={parsed.port or 5432} "
        f"user={parsed.username} password={parsed.password}"
    )
    writer = duckdb.connect()
    writer.execute("INSTALL ducklake")
    writer.execute("LOAD ducklake")
    writer.execute("INSTALL postgres")
    writer.execute("LOAD postgres")
    writer.execute(
        f"ATTACH 'ducklake:postgres:{dsn}' AS lk "
        f"(DATA_PATH '{tmp_path}/', METADATA_SCHEMA '{catalog.metadata_schema}')"
    )
    writer.execute("USE lk")
    writer.execute("CREATE TABLE t (a INTEGER)")
    try:
        yield backend, catalog, writer
    finally:
        writer.close()
        await backend.deprovision(catalog)
        await dispose_engine()
        settings.ducklake_enabled, settings.ducklake_database_url = original


async def _version(backend, catalog) -> TableVersion:
    return (await backend.table_versions(catalog, [("main", "t")]))[("main", "t")]


@pytest.mark.parametrize(
    ("setup", "rewrite"),
    [
        # Inlined rows moved into a Parquet file.
        (["INSERT INTO t VALUES (1), (2)"], "CALL ducklake_flush_inlined_data('lk')"),
        # Small files merged into one.
        (
            [
                "INSERT INTO t SELECT i FROM range(100) r(i)",
                "INSERT INTO t SELECT i FROM range(100) r(i)",
            ],
            "CALL ducklake_merge_adjacent_files('lk')",
        ),
        # Files rewritten to drop deleted rows (only once most of a file is deleted).
        (
            ["INSERT INTO t SELECT i FROM range(100) r(i)", "DELETE FROM t WHERE a < 99"],
            "CALL ducklake_rewrite_data_files('lk', 't')",
        ),
    ],
)
async def test_file_rewrites_are_data_equivalent(ducklake, setup: list[str], rewrite: str) -> None:
    backend, catalog, writer = ducklake
    for sql in setup:
        writer.execute(sql)
    before = await _version(backend, catalog)
    writer.execute(rewrite)
    after = await _version(backend, catalog)
    assert after != before
    assert await backend.data_equivalent(catalog, "main", "t", before, after)


async def test_flushing_inlined_deletions_counts_as_a_change(ducklake) -> None:
    """DuckLake records that flush as `deleted_from_table`, the same token a real
    DELETE leaves, so it cannot be told apart: a cache miss, never a stale hit."""
    backend, catalog, writer = ducklake
    writer.execute("INSERT INTO t SELECT i FROM range(100) r(i)")
    writer.execute("DELETE FROM t WHERE a = 5")
    before = await _version(backend, catalog)
    writer.execute("CALL ducklake_flush_inlined_data('lk')")
    after = await _version(backend, catalog)
    assert after != before
    assert not await backend.data_equivalent(catalog, "main", "t", before, after)


async def test_writes_and_schema_changes_are_changes(ducklake) -> None:
    backend, catalog, writer = ducklake
    v1 = await _version(backend, catalog)
    writer.execute("INSERT INTO t VALUES (1)")
    v2 = await _version(backend, catalog)
    assert v2 != v1
    assert not await backend.data_equivalent(catalog, "main", "t", v1, v2)
    writer.execute("ALTER TABLE t ADD COLUMN b VARCHAR")
    v3 = await _version(backend, catalog)
    assert v3 != v2
    assert not await backend.data_equivalent(catalog, "main", "t", v2, v3)


async def test_other_tables_do_not_move_this_version(ducklake) -> None:
    backend, catalog, writer = ducklake
    before = await _version(backend, catalog)
    writer.execute("CREATE TABLE u AS SELECT 1 AS x")
    writer.execute("INSERT INTO u VALUES (2)")
    assert await _version(backend, catalog) == before


async def test_a_missing_table_is_absent_from_ducklake(ducklake) -> None:
    backend, catalog, _ = ducklake
    versions = await backend.table_versions(catalog, [("main", "t"), ("main", "nope")])
    assert set(versions) == {("main", "t")}


async def test_a_macro_change_moves_the_routines_version(ducklake) -> None:
    backend, catalog, writer = ducklake
    before = await backend.routines_version(catalog)
    writer.execute("CREATE MACRO upper(x) AS 42")
    after = await backend.routines_version(catalog)
    assert after != before
    writer.execute("DROP MACRO upper")
    assert await backend.routines_version(catalog) not in (before, after)
