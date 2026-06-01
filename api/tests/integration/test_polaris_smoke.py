"""Polaris reachability + catalog/namespace/table CRUD via PolarisClient.

Opt-in (`-m integration`); requires a live Polaris (see conftest). Validates
that the production `PolarisClient` speaks the Iceberg REST + management
contract the routers depend on.
"""

from __future__ import annotations

import pytest

from api.services.polaris import PolarisClient, PolarisNotFoundError

pytestmark = pytest.mark.integration


async def test_catalog_exists_and_namespace_roundtrip(
    polaris: PolarisClient, unique_catalog: str
) -> None:
    assert await polaris.catalog_exists(unique_catalog) is True
    assert await polaris.catalog_exists("definitely_missing_catalog") is False

    await polaris.create_schema(unique_catalog, "main")
    names = [s.name for s in await polaris.list_schemas(unique_catalog)]
    assert "main" in names


async def test_table_create_get_delete(
    polaris: PolarisClient, unique_catalog: str, unique_name: str
) -> None:
    await polaris.create_schema(unique_catalog, "main")
    await polaris.create_table(
        catalog=unique_catalog,
        schema="main",
        name=unique_name,
        columns=[
            {"id": 1, "name": "id", "required": True, "type": "long"},
            {"id": 2, "name": "label", "required": False, "type": "string"},
        ],
    )

    got = await polaris.get_table(unique_catalog, "main", unique_name)
    assert got.data_source_format == "ICEBERG"
    assert [(c.name, c.type_name) for c in got.columns] == [("id", "LONG"), ("label", "STRING")]
    assert unique_name in [t.name for t in await polaris.list_tables(unique_catalog, "main")]

    await polaris.delete_table(unique_catalog, "main", unique_name)
    with pytest.raises(PolarisNotFoundError):
        await polaris.get_table(unique_catalog, "main", unique_name)
