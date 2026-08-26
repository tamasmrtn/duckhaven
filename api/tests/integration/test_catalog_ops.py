"""Namespace + table CRUD through the API against real Polaris.

Drives the schemas/tables routers end-to-end: every call hits the live Polaris
catalog provisioned by the workspace fixture. No FakePolaris.
"""

from __future__ import annotations

import pytest
import pytest_asyncio

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def schemas_base(admin_client, workspace_factory) -> str:
    """The catalog-scoped schemas path for a fresh workspace.

    Returns the base rather than the slug because schemas and tables are always
    addressed under their catalog -- the default-catalog shim that let it be
    omitted was removed at api_version 2.
    """
    ws = await workspace_factory(name="Catalog Ops")
    slug = ws["slug"]
    # Workspaces do not auto-create a catalog, so attach one to address.
    created = await admin_client.post(
        f"/workspaces/{slug}/catalogs", json={"name": f"c_{slug.replace('-', '_')}"}
    )
    assert created.status_code == 201, created.text
    return f"/workspaces/{slug}/catalogs/{created.json()['slug']}/schemas"


async def test_schema_create_list_drop(admin_client, schemas_base) -> None:
    base = schemas_base

    created = await admin_client.post(base, json={"name": "marketing"})
    assert created.status_code == 201, created.text

    names = {s["name"] for s in (await admin_client.get(base)).json()}
    assert {"analytics", "marketing"} <= names

    # Creating the same namespace again conflicts.
    assert (await admin_client.post(base, json={"name": "marketing"})).status_code == 409

    dropped = await admin_client.delete(f"{base}/marketing")
    assert dropped.status_code == 204
    names_after = {s["name"] for s in (await admin_client.get(base)).json()}
    assert "marketing" not in names_after


async def test_table_create_get_list_drop(admin_client, schemas_base) -> None:
    tables_url = f"{schemas_base}/analytics/tables"
    body = {
        "name": "orders",
        "columns": [
            {"name": "id", "type": "BIGINT", "nullable": False},
            {"name": "total", "type": "DECIMAL", "nullable": True},
            {"name": "label", "type": "VARCHAR", "nullable": True},
        ],
    }
    created = await admin_client.post(tables_url, json=body)
    assert created.status_code == 201, created.text
    cols = {c["name"]: c for c in created.json()["columns"]}
    assert set(cols) == {"id", "total", "label"}
    assert cols["id"]["nullable"] is False

    got = await admin_client.get(f"{tables_url}/orders")
    assert got.status_code == 200
    assert got.json()["name"] == "orders"
    # Iceberg-native metadata is surfaced from the live Polaris LoadTableResult.
    assert got.json()["format_version"] is not None

    listed = await admin_client.get(tables_url)
    assert "orders" in {t["name"] for t in listed.json()}

    dropped = await admin_client.delete(f"{tables_url}/orders")
    assert dropped.status_code == 204
    assert (await admin_client.get(f"{tables_url}/orders")).status_code == 404


async def test_create_table_rejects_unknown_type(admin_client, schemas_base) -> None:
    resp = await admin_client.post(
        f"{schemas_base}/analytics/tables",
        json={"name": "bad", "columns": [{"name": "c", "type": "JSON"}]},
    )
    assert resp.status_code == 422  # not in the AllowedColumnType set


async def test_catalog_endpoints_require_auth(app_client, schemas_base) -> None:
    # The schemas_base fixture authenticated the shared client; drop the
    # session cookie to assert the unauthenticated path is rejected.
    app_client.cookies.clear()
    assert (await app_client.get(schemas_base)).status_code == 401
