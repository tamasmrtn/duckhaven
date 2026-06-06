"""Namespace + table CRUD through the API against real Polaris.

Drives the schemas/tables routers end-to-end: every call hits the live Polaris
catalog provisioned by the workspace fixture. No FakePolaris.
"""

from __future__ import annotations

import pytest
import pytest_asyncio

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def workspace_slug(workspace_factory) -> str:
    ws = await workspace_factory(name="Catalog Ops")
    return ws["slug"]


async def test_schema_create_list_drop(admin_client, workspace_slug) -> None:
    base = f"/workspaces/{workspace_slug}/schemas"

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


async def test_table_create_get_list_drop(admin_client, workspace_slug) -> None:
    tables_url = f"/workspaces/{workspace_slug}/schemas/analytics/tables"
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


async def test_create_table_rejects_unknown_type(admin_client, workspace_slug) -> None:
    resp = await admin_client.post(
        f"/workspaces/{workspace_slug}/schemas/analytics/tables",
        json={"name": "bad", "columns": [{"name": "c", "type": "JSON"}]},
    )
    assert resp.status_code == 422  # not in the AllowedColumnType set


async def test_catalog_endpoints_require_auth(app_client, workspace_slug) -> None:
    # The workspace_slug fixture authenticated the shared client; drop the
    # session cookie to assert the unauthenticated path is rejected.
    app_client.cookies.clear()
    assert (await app_client.get(f"/workspaces/{workspace_slug}/schemas")).status_code == 401
