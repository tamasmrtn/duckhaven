"""Command-palette search against real Postgres + real Polaris.

Unit tests (api/tests/unit/routers/test_search.py) cover permission
boundaries against FakePolaris; this exercises the real fan-out over a live
Polaris catalog plus a real saved-query row, end to end.
"""

from __future__ import annotations

import pytest
import pytest_asyncio

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def workspace(admin_client, workspace_factory) -> tuple[str, str]:
    """A workspace with a catalog attached, as `(workspace_slug, catalog_slug)`.

    Both are needed because schemas and tables are addressed under their
    catalog; only the workspace slug is needed for search itself.
    """
    ws = await workspace_factory(name="Search Ops")
    slug = ws["slug"]
    created = await admin_client.post(
        f"/workspaces/{slug}/catalogs", json={"name": f"c_{slug.replace('-', '_')}"}
    )
    assert created.status_code == 201, created.text
    return slug, created.json()["slug"]


async def test_search_matches_table_schema_and_saved_query(admin_client, workspace) -> None:
    workspace_slug, catalog = workspace
    schemas_url = f"/workspaces/{workspace_slug}/catalogs/{catalog}/schemas"
    await admin_client.post(schemas_url, json={"name": "marketing"})
    table = await admin_client.post(
        f"{schemas_url}/marketing/tables",
        json={
            "name": "leads",
            "columns": [{"name": "id", "type": "BIGINT", "nullable": False}],
        },
    )
    assert table.status_code == 201, table.text
    saved = await admin_client.post(
        f"/workspaces/{workspace_slug}/saved-queries",
        json={"name": "Lead funnel report", "sql": "SELECT 1"},
    )
    assert saved.status_code == 201, saved.text

    # Search returns a report envelope, not a bare array: `items` plus the
    # `has_more` that says the result set was truncated by `limit`.
    results = (
        await admin_client.get(f"/workspaces/{workspace_slug}/search", params={"q": "lead"})
    ).json()["items"]

    by_type = {r["type"]: r for r in results if r["type"] != "schema"}
    assert by_type["table"]["name"] == "leads"
    assert by_type["table"]["schema_name"] == "marketing"
    assert by_type["saved_query"]["name"] == "Lead funnel report"

    schema_results = [r for r in results if r["type"] == "schema"]
    assert not schema_results  # "lead" doesn't match "marketing" or "analytics"


async def test_search_endpoint_requires_auth(app_client, workspace) -> None:
    # The workspace fixture authenticated the shared client; drop the session
    # cookie to assert the unauthenticated path is rejected.
    workspace_slug, _ = workspace
    app_client.cookies.clear()
    resp = await app_client.get(f"/workspaces/{workspace_slug}/search", params={"q": "x"})
    assert resp.status_code == 401
