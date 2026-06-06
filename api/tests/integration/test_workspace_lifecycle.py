"""Workspace creation against real Postgres + real Polaris.

A name-only workspace auto-provisions a bundled object-store backend and
eagerly creates its Polaris catalog + default namespace. These tests assert the
control-plane row *and* the real Polaris catalog land together, and that the
failure/auth edges behave.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from api.services.polaris import PolarisClient

pytestmark = pytest.mark.integration


async def test_create_workspace_provisions_polaris_catalog(
    admin_client, workspace_factory, polaris: PolarisClient
) -> None:
    slug = f"dh-it-{uuid4().hex[:8]}"
    ws = await workspace_factory(slug=slug, name="Analytics")
    assert ws["slug"] == slug
    assert ws["storage_backend_kind"] == "object_store"

    # The catalog exists in real Polaris, and its default namespace is present.
    assert await polaris.catalog_exists(slug)
    schemas = await polaris.list_schemas(slug)
    assert "analytics" in {s.name for s in schemas}

    listed = await admin_client.get("/workspaces")
    assert listed.status_code == 200
    assert slug in {w["slug"] for w in listed.json()}

    detail = await admin_client.get(f"/workspaces/{slug}")
    assert detail.status_code == 200
    assert detail.json()["slug"] == slug


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
