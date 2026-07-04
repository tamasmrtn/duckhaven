"""Scoped-mode grant enforcement on the browsing endpoints (issue #129)."""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy import select, update

from api.models.catalog import Catalog, WorkspaceCatalog
from api.models.catalog_grant import CatalogGrant
from api.models.storage_backend import StorageBackend
from api.models.user import User
from api.models.workspace import Workspace, WorkspaceMember
from api.services.auth import hash_password


@pytest.fixture
async def owner(db_session) -> User:
    u = User(email="owner@test.local", password_hash=hash_password("pw"), name="Owner", role="user")
    db_session.add(u)
    await db_session.commit()
    await db_session.refresh(u)
    return u


@pytest.fixture
async def auth_client(client: AsyncClient, owner: User) -> AsyncClient:
    await client.post("/auth/login", json={"email": "owner@test.local", "password": "pw"})
    return client


_COLS = [{"name": "id", "type": "INTEGER", "nullable": True}]


async def _login(client: AsyncClient, email: str) -> None:
    await client.post("/auth/login", json={"email": email, "password": "pw"})


async def _scoped_env(auth_client, owner, db_session, fake_polaris):
    """Owner-created ws with marketing.leads + finance.ledger, then flipped to
    scoped. Returns (slug, workspace, catalog, member) — member is a reader with
    no grants yet."""
    resp = await auth_client.post("/workspaces", json={"slug": "alpha", "name": "Alpha"})
    assert resp.status_code == 201, resp.text
    slug = resp.json()["slug"]
    sb = StorageBackend(kind="object_store", name="primary", root_uri="", created_by=owner.id)
    db_session.add(sb)
    await db_session.commit()
    await db_session.refresh(sb)
    cat_resp = await auth_client.post(
        f"/workspaces/{slug}/catalogs", json={"name": "alpha", "storage_backend_id": str(sb.id)}
    )
    assert cat_resp.status_code == 201, cat_resp.text

    for schema, table in [("marketing", "leads"), ("finance", "ledger")]:
        await auth_client.post(f"/workspaces/{slug}/schemas", json={"name": schema})
        r = await auth_client.post(
            f"/workspaces/{slug}/schemas/{schema}/tables", json={"name": table, "columns": _COLS}
        )
        assert r.status_code == 201, r.text

    ws = (await db_session.execute(select(Workspace).where(Workspace.slug == slug))).scalar_one()
    cat = (
        (await db_session.execute(select(Catalog).where(Catalog.workspace_links.any())))
        .scalars()
        .first()
    )

    member = User(
        email="member@test.local", password_hash=hash_password("pw"), name="Member", role="user"
    )
    db_session.add(member)
    await db_session.commit()
    await db_session.refresh(member)
    db_session.add(WorkspaceMember(workspace_id=ws.id, user_id=member.id, role="reader"))
    await db_session.execute(
        update(WorkspaceCatalog)
        .where(WorkspaceCatalog.workspace_id == ws.id, WorkspaceCatalog.catalog_id == cat.id)
        .values(access_mode="scoped")
    )
    await db_session.commit()
    return slug, ws, cat, member


def _grant(db_session, member, cat, tier, schema=None, table=None):
    db_session.add(
        CatalogGrant(
            user_id=member.id, catalog_id=cat.id, schema_name=schema, table_name=table, tier=tier
        )
    )


async def test_metadata_tier_browses_but_cannot_sample(
    auth_client, owner, db_session, fake_polaris
):
    slug, ws, cat, member = await _scoped_env(auth_client, owner, db_session, fake_polaris)
    _grant(db_session, member, cat, "metadata", schema="marketing", table="leads")
    await db_session.commit()
    await _login(auth_client, member.email)

    # metadata: describe the table.
    r = await auth_client.get(f"/workspaces/{slug}/schemas/marketing/tables/leads")
    assert r.status_code == 200
    # but not its rows.
    r = await auth_client.get(f"/workspaces/{slug}/schemas/marketing/tables/leads/sample")
    assert r.status_code == 404


async def test_lists_are_filtered_to_granted_nodes(auth_client, owner, db_session, fake_polaris):
    slug, ws, cat, member = await _scoped_env(auth_client, owner, db_session, fake_polaris)
    _grant(db_session, member, cat, "reader", schema="marketing", table="leads")
    await db_session.commit()
    await _login(auth_client, member.email)

    schemas = {s["name"] for s in (await auth_client.get(f"/workspaces/{slug}/schemas")).json()}
    assert "marketing" in schemas and "finance" not in schemas

    tables = {
        t["name"]
        for t in (await auth_client.get(f"/workspaces/{slug}/schemas/marketing/tables")).json()
    }
    assert tables == {"leads"}

    # A table with no grant is a 404, not a 403 — indistinguishable from missing.
    r = await auth_client.get(f"/workspaces/{slug}/schemas/finance/tables/ledger")
    assert r.status_code == 404


async def test_reader_grant_passes_the_sample_gate(auth_client, owner, db_session, fake_polaris):
    slug, ws, cat, member = await _scoped_env(auth_client, owner, db_session, fake_polaris)
    _grant(db_session, member, cat, "reader", schema="marketing")
    await db_session.commit()
    await _login(auth_client, member.email)

    # reader clears the grant gate; it then fails at "no agent connected" (503),
    # not the 404 a metadata-tier principal would get.
    r = await auth_client.get(f"/workspaces/{slug}/schemas/marketing/tables/leads/sample")
    assert r.status_code == 503


async def test_schema_grant_covers_future_table(auth_client, owner, db_session, fake_polaris):
    slug, ws, cat, member = await _scoped_env(auth_client, owner, db_session, fake_polaris)
    _grant(db_session, member, cat, "metadata", schema="marketing")
    await db_session.commit()
    # A table created *after* the grant, seeded straight into Polaris.
    await fake_polaris.create_table(
        catalog=cat.polaris_name, schema="marketing", name="new_leads", columns=[]
    )
    await _login(auth_client, member.email)

    r = await auth_client.get(f"/workspaces/{slug}/schemas/marketing/tables/new_leads")
    assert r.status_code == 200


async def test_reader_role_cannot_be_promoted_by_writer_grant(
    auth_client, owner, db_session, fake_polaris
):
    slug, ws, cat, member = await _scoped_env(auth_client, owner, db_session, fake_polaris)
    # member is a workspace `reader`; a writer grant must not let them write —
    # the workspace boundary 403s a reader before the grant is even consulted.
    _grant(db_session, member, cat, "writer", schema="marketing")
    await db_session.commit()
    await _login(auth_client, member.email)

    r = await auth_client.post(
        f"/workspaces/{slug}/schemas/marketing/tables", json={"name": "t2", "columns": _COLS}
    )
    assert r.status_code == 403


async def test_grant_narrows_writer_role_down(auth_client, owner, db_session, fake_polaris):
    slug, ws, cat, member = await _scoped_env(auth_client, owner, db_session, fake_polaris)
    # Promote member to workspace `writer`, but grant only `reader` on marketing:
    # the grant narrows them, so a create (needs writer tier) is 404.
    await db_session.execute(
        update(WorkspaceMember)
        .where(WorkspaceMember.workspace_id == ws.id, WorkspaceMember.user_id == member.id)
        .values(role="writer")
    )
    _grant(db_session, member, cat, "reader", schema="marketing")
    await db_session.commit()
    await _login(auth_client, member.email)

    r = await auth_client.post(
        f"/workspaces/{slug}/schemas/marketing/tables", json={"name": "t2", "columns": _COLS}
    )
    assert r.status_code == 404


async def test_drop_table_cleans_up_grants(auth_client, owner, db_session, fake_polaris):
    slug, ws, cat, member = await _scoped_env(auth_client, owner, db_session, fake_polaris)
    _grant(db_session, member, cat, "reader", schema="marketing", table="leads")
    # Owner needs writer to drop; grant catalog-level so owner keeps access.
    db_session.add(
        CatalogGrant(
            user_id=owner.id, catalog_id=cat.id, schema_name=None, table_name=None, tier="writer"
        )
    )
    await db_session.commit()

    r = await auth_client.delete(f"/workspaces/{slug}/schemas/marketing/tables/leads")
    assert r.status_code == 204
    remaining = (
        (
            await db_session.execute(
                select(CatalogGrant).where(
                    CatalogGrant.catalog_id == cat.id,
                    CatalogGrant.schema_name == "marketing",
                    CatalogGrant.table_name == "leads",
                )
            )
        )
        .scalars()
        .all()
    )
    assert remaining == []
