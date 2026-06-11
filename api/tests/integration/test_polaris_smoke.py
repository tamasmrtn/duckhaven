"""Polaris reachability + catalog/namespace/table CRUD via PolarisClient.

Opt-in (`-m integration`); requires a live Polaris (see conftest). Validates
that the production `PolarisClient` speaks the Iceberg REST + management
contract the routers depend on. These exercise only the catalog REST API
(no DuckDB), so they are independent of the storage backend.
"""

from __future__ import annotations

import pytest

from api.services.polaris import (
    PolarisClient,
    PolarisConflictError,
    PolarisNotFoundError,
)

pytestmark = pytest.mark.integration


def _cols() -> list[dict]:
    return [
        {"id": 1, "name": "id", "required": True, "type": "long"},
        {"id": 2, "name": "label", "required": False, "type": "string"},
    ]


# --- catalogs ---


async def test_catalog_exists_true_and_false(polaris: PolarisClient, unique_catalog: str) -> None:
    assert await polaris.catalog_exists(unique_catalog) is True
    assert await polaris.catalog_exists("definitely_missing_catalog") is False


async def test_create_catalog_conflict(
    polaris: PolarisClient, unique_catalog: str, s3_catalog_storage: tuple[str, dict]
) -> None:
    # The fixture already created it; a second create must conflict.
    bucket, extra = s3_catalog_storage
    with pytest.raises(PolarisConflictError):
        await polaris.create_catalog(
            unique_catalog,
            storage_type="S3",
            base_location=f"{bucket}/{unique_catalog}",
            extra_storage=extra,
        )


async def test_get_catalog_not_found(polaris: PolarisClient) -> None:
    with pytest.raises(PolarisNotFoundError):
        await polaris.get_catalog("nope_missing_catalog")


async def test_ensure_catalog_access_is_idempotent(
    polaris: PolarisClient, unique_catalog: str
) -> None:
    """Grants are wired on first call; the second re-grants existing grants, which
    Polaris answers with a duplicate-key 500 the client must treat as a no-op."""
    await polaris.ensure_catalog_access(unique_catalog)
    # Must not raise — and grants must remain intact, so a namespace create still works.
    await polaris.ensure_catalog_access(unique_catalog)
    await polaris.create_schema(unique_catalog, "main")
    assert "main" in [s.name for s in await polaris.list_schemas(unique_catalog)]


# --- namespaces ---


async def test_namespace_create_list_and_conflict(
    polaris: PolarisClient, unique_catalog: str
) -> None:
    await polaris.create_schema(unique_catalog, "main")
    names = [s.name for s in await polaris.list_schemas(unique_catalog)]
    assert "main" in names
    # Re-creating the same namespace conflicts.
    with pytest.raises(PolarisConflictError):
        await polaris.create_schema(unique_catalog, "main")


# --- tables ---


async def test_table_create_get_list_delete(
    polaris: PolarisClient, unique_catalog: str, unique_name: str
) -> None:
    await polaris.create_schema(unique_catalog, "main")
    created = await polaris.create_table(
        catalog=unique_catalog, schema="main", name=unique_name, columns=_cols()
    )
    assert created.data_source_format == "ICEBERG"

    # get_table returns full columns mapped from the Iceberg schema.
    got = await polaris.get_table(unique_catalog, "main", unique_name)
    assert got.table_id is not None
    assert [(c.name, c.type_name, c.nullable) for c in got.columns] == [
        ("id", "LONG", False),
        ("label", "STRING", True),
    ]

    # list_tables returns identifiers only (no columns) per Iceberg REST.
    listed = await polaris.list_tables(unique_catalog, "main")
    assert unique_name in [t.name for t in listed]
    assert all(t.columns == [] for t in listed)

    await polaris.delete_table(unique_catalog, "main", unique_name)
    with pytest.raises(PolarisNotFoundError):
        await polaris.get_table(unique_catalog, "main", unique_name)


async def test_create_table_conflict(
    polaris: PolarisClient, unique_catalog: str, unique_name: str
) -> None:
    await polaris.create_schema(unique_catalog, "main")
    await polaris.create_table(
        catalog=unique_catalog, schema="main", name=unique_name, columns=_cols()
    )
    with pytest.raises(PolarisConflictError):
        await polaris.create_table(
            catalog=unique_catalog, schema="main", name=unique_name, columns=_cols()
        )


async def test_get_table_not_found(polaris: PolarisClient, unique_catalog: str) -> None:
    await polaris.create_schema(unique_catalog, "main")
    with pytest.raises(PolarisNotFoundError):
        await polaris.get_table(unique_catalog, "main", "ghost_table")


async def test_list_snapshots_reads_live_load_table(
    polaris: PolarisClient, unique_catalog: str, unique_name: str
) -> None:
    """The snapshot read parses the live LoadTableResult. A freshly created
    table has had no data written, so its snapshot log is empty — this
    confirms the production read path against real Polaris metadata."""
    await polaris.create_schema(unique_catalog, "main")
    await polaris.create_table(
        catalog=unique_catalog, schema="main", name=unique_name, columns=_cols()
    )
    assert await polaris.list_snapshots(unique_catalog, "main", unique_name) == []
    with pytest.raises(PolarisNotFoundError):
        await polaris.list_snapshots(unique_catalog, "main", "ghost_table")


async def test_delete_table_not_found(polaris: PolarisClient, unique_catalog: str) -> None:
    await polaris.create_schema(unique_catalog, "main")
    with pytest.raises(PolarisNotFoundError):
        await polaris.delete_table(unique_catalog, "main", "ghost_table")
