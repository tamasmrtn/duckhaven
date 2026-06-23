"""Workspace + catalog creation against real Postgres + real Polaris.

A name-only workspace now starts empty (no catalog, no storage). Creating a
catalog eagerly provisions its real Polaris catalog + default namespace. These
tests assert the control-plane row *and* the real Polaris catalog land together,
and that the failure/auth edges behave.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from api.services.polaris import PolarisClient

pytestmark = pytest.mark.integration


async def test_workspace_starts_empty_then_catalog_provisions_polaris(
    admin_client, workspace_factory, polaris: PolarisClient
) -> None:
    slug = f"dh-it-{uuid4().hex[:8]}"
    ws = await workspace_factory(slug=slug, name="Analytics")
    assert ws["slug"] == slug
    # A new workspace has no catalog and no storage yet.
    assert ws["default_catalog"] is None
    assert ws["storage_backend_kind"] is None

    cat = f"c_{slug.replace('-', '_')}"
    created = await admin_client.post(f"/workspaces/{slug}/catalogs", json={"name": cat})
    assert created.status_code == 201, created.text
    assert created.json()["slug"] == cat
    assert created.json()["storage_backend_kind"] == "object_store"

    # The catalog exists in real Polaris, and its default namespace is present.
    assert await polaris.catalog_exists(cat)
    schemas = await polaris.list_schemas(cat)
    assert "analytics" in {s.name for s in schemas}

    # The workspace now reports the catalog as its default.
    detail = await admin_client.get(f"/workspaces/{slug}")
    assert detail.status_code == 200
    assert detail.json()["default_catalog"] == cat


async def test_duplicate_slug_conflicts(admin_client, workspace_factory) -> None:
    slug = f"dh-it-{uuid4().hex[:8]}"
    await workspace_factory(slug=slug, name="First")
    dup = await admin_client.post("/workspaces", json={"slug": slug, "name": "Second"})
    assert dup.status_code == 409


async def test_workspace_endpoints_require_auth(app_client) -> None:
    assert (await app_client.get("/workspaces")).status_code == 401
    create = await app_client.post("/workspaces", json={"slug": "x", "name": "x"})
    assert create.status_code == 401


async def test_get_unknown_workspace_is_404(admin_client) -> None:
    resp = await admin_client.get(f"/workspaces/missing-{uuid4().hex[:8]}")
    assert resp.status_code == 404
