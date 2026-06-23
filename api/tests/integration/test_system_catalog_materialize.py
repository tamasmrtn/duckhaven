"""Integration test: the PyIceberg materializer writer against a live Polaris.

Env-gated (POLARIS_BASE_URL + POLARIS_S3_BUCKET); skips cleanly otherwise. Proves
the control plane can create + append to the system catalog's Iceberg tables
through Polaris with access delegation, and read the rows back.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime

import pytest

from api.services.system_catalog import tables
from api.services.system_catalog.writer import IcebergSystemCatalogWriter

pytestmark = pytest.mark.integration


@pytest.fixture
async def system_warehouse(polaris, s3_catalog_storage):
    """A throwaway Polaris catalog (with write grants) standing in for the real
    ``duckhaven`` system warehouse, torn down on exit."""
    from uuid import uuid4

    bucket, extra = s3_catalog_storage
    name = f"dh_sys_it_{uuid4().hex[:10]}"
    await polaris.create_catalog(
        name, storage_type="S3", base_location=f"{bucket}/{name}", extra_storage=extra
    )
    await polaris.ensure_catalog_access(name)
    try:
        yield name
    finally:
        try:
            for schema in await polaris.list_schemas(name):
                for tbl in await polaris.list_tables(name, schema.name):
                    await polaris.delete_table(name, schema.name, tbl.name, purge=True)
                await polaris.delete_schema(name, schema.name)
            await polaris.delete_catalog_access(name)
            await polaris.delete_catalog(name)
        except Exception:  # noqa: BLE001 - best-effort teardown
            pass


async def test_writer_appends_and_reads_back(polaris_base_url, system_warehouse):
    writer = IcebergSystemCatalogWriter(
        base_url=polaris_base_url,
        realm=os.getenv("POLARIS_REALM", "POLARIS"),
        client_id=os.getenv("POLARIS_CLIENT_ID", "root"),
        client_secret=os.getenv("POLARIS_CLIENT_SECRET", "s3cr3t"),
        warehouse=system_warehouse,
    )
    now = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
    row = {
        "query_id": "q-1",
        "workspace_id": "w-1",
        "workspace_slug": "dev",
        "agent_id": None,
        "agent_name": None,
        "user_id": "u-1",
        "user_email": "u@test.local",
        "statement_type": "SELECT",
        "status": "done",
        "origin": None,
        "row_count": 5,
        "result_bytes": 100,
        "duration_ms": 200,
        "reserved_memory_bytes": 1024,
        "reserved_threads": 4,
        "error": None,
        "started_at": now,
        "finished_at": now,
    }
    writer.append(tables.QUERY_HISTORY, [row])

    # Read it back through PyIceberg (proves it is real, queryable Iceberg data).
    table = writer._rest_catalog().load_table(tables.QUERY_HISTORY.identifier)
    fetched = table.scan().to_arrow().to_pylist()
    assert [r["query_id"] for r in fetched] == ["q-1"]
    assert fetched[0]["workspace_slug"] == "dev"


async def test_overwrite_replaces_snapshot(polaris_base_url, system_warehouse):
    writer = IcebergSystemCatalogWriter(
        base_url=polaris_base_url,
        realm=os.getenv("POLARIS_REALM", "POLARIS"),
        client_id=os.getenv("POLARIS_CLIENT_ID", "root"),
        client_secret=os.getenv("POLARIS_CLIENT_SECRET", "s3cr3t"),
        warehouse=system_warehouse,
    )
    writer.overwrite(tables.INFO_CATALOGS, [_catalog_row("a")])
    writer.overwrite(tables.INFO_CATALOGS, [_catalog_row("b"), _catalog_row("c")])

    table = writer._rest_catalog().load_table(tables.INFO_CATALOGS.identifier)
    fetched = {r["catalog"] for r in table.scan().to_arrow().to_pylist()}
    assert fetched == {"b", "c"}


def _catalog_row(slug: str) -> dict:
    return {
        "catalog": slug,
        "polaris_name": slug,
        "storage_kind": "object_store",
        "is_system": False,
        "created_at": datetime(2026, 1, 1, tzinfo=UTC),
    }
