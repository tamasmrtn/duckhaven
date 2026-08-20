"""Unit tests for the command-palette search endpoint."""

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

_COLS = [{"name": "id", "type": "INTEGER", "nullable": True}]


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


async def _login(client: AsyncClient, email: str) -> None:
    await client.post("/auth/login", json={"email": email, "password": "pw"})


async def _make_workspace(
    auth_client: AsyncClient, backend: StorageBackend, slug: str = "alpha"
) -> str:
    resp = await auth_client.post("/workspaces", json={"slug": slug, "name": slug.title()})
    assert resp.status_code == 201, resp.text
    cat = await auth_client.post(
        f"/workspaces/{slug}/catalogs",
        json={"name": slug.replace("-", "_"), "storage_backend_id": str(backend.id)},
    )
    assert cat.status_code == 201, cat.text
    return resp.json()["slug"]


@pytest.fixture
async def backend(db_session, owner: User) -> StorageBackend:
    sb = StorageBackend(kind="object_store", name="primary", root_uri="", created_by=owner.id)
    db_session.add(sb)
    await db_session.commit()
    await db_session.refresh(sb)
    return sb


async def test_matches_tables_and_schemas_by_substring(
    auth_client: AsyncClient, backend: StorageBackend, fake_polaris
):
    slug = await _make_workspace(auth_client, backend)
    await auth_client.post(f"/workspaces/{slug}/schemas", json={"name": "marketing"})
    await auth_client.post(
        f"/workspaces/{slug}/schemas/marketing/tables",
        json={"name": "leads", "columns": _COLS},
    )

    resp = await auth_client.get(f"/workspaces/{slug}/search", params={"q": "lead"})
    assert resp.status_code == 200
    results = resp.json()
    assert {
        "type": "table",
        "catalog": slug.replace("-", "_"),
        "schema_name": "marketing",
        "name": "leads",
    }.items() <= next(r for r in results if r["type"] == "table").items()

    resp = await auth_client.get(f"/workspaces/{slug}/search", params={"q": "market"})
    schema_hit = next(r for r in resp.json() if r["type"] == "schema")
    assert schema_hit["name"] == "marketing"


async def test_matches_saved_queries_by_name(
    auth_client: AsyncClient, backend: StorageBackend, fake_polaris
):
    slug = await _make_workspace(auth_client, backend)
    resp = await auth_client.post(
        f"/workspaces/{slug}/saved-queries",
        json={"name": "Daily revenue report", "sql": "SELECT 1"},
    )
    assert resp.status_code == 201, resp.text

    results = (await auth_client.get(f"/workspaces/{slug}/search", params={"q": "revenue"})).json()
    hit = next(r for r in results if r["type"] == "saved_query")
    assert hit["name"] == "Daily revenue report"
    assert hit["sql"] == "SELECT 1"
    assert hit["id"] is not None


async def test_empty_query_returns_nothing(
    auth_client: AsyncClient, backend: StorageBackend, fake_polaris
):
    slug = await _make_workspace(auth_client, backend)
    resp = await auth_client.get(f"/workspaces/{slug}/search", params={"q": ""})
    assert resp.status_code == 200
    assert resp.json() == []


async def test_no_match_returns_empty_list(
    auth_client: AsyncClient, backend: StorageBackend, fake_polaris
):
    slug = await _make_workspace(auth_client, backend)
    resp = await auth_client.get(f"/workspaces/{slug}/search", params={"q": "nonexistent_xyz"})
    assert resp.status_code == 200
    assert resp.json() == []


async def test_limit_is_capped(auth_client: AsyncClient, backend: StorageBackend, fake_polaris):
    slug = await _make_workspace(auth_client, backend)
    resp = await auth_client.get(f"/workspaces/{slug}/search", params={"q": "a", "limit": 500})
    assert resp.status_code == 422


async def test_non_member_is_forbidden(
    auth_client: AsyncClient, backend: StorageBackend, db_session, fake_polaris
):
    slug = await _make_workspace(auth_client, backend)
    stranger = User(
        email="stranger@test.local", password_hash=hash_password("pw"), name="Stranger", role="user"
    )
    db_session.add(stranger)
    await db_session.commit()
    await _login(auth_client, stranger.email)

    resp = await auth_client.get(f"/workspaces/{slug}/search", params={"q": "a"})
    assert resp.status_code == 403


async def test_search_respects_scoped_catalog_grants(
    auth_client: AsyncClient, owner: User, db_session, fake_polaris
):
    """A reader with a grant on only one table must not see a same-substring
    match under a different, ungranted schema — mirrors
    test_schema_grants.py's test_lists_are_filtered_to_granted_nodes, applied
    to search instead of the plain list endpoints."""
    sb = StorageBackend(kind="object_store", name="primary", root_uri="", created_by=owner.id)
    db_session.add(sb)
    await db_session.commit()
    await db_session.refresh(sb)

    resp = await auth_client.post("/workspaces", json={"slug": "alpha", "name": "Alpha"})
    assert resp.status_code == 201, resp.text
    slug = resp.json()["slug"]
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
    db_session.add(
        CatalogGrant(
            user_id=member.id,
            catalog_id=cat.id,
            schema_name="marketing",
            table_name="leads",
            tier="reader",
        )
    )
    await db_session.commit()
    await _login(auth_client, member.email)

    # "e" matches both leads (granted) and ledger (ungranted, different schema).
    results = (await auth_client.get(f"/workspaces/{slug}/search", params={"q": "e"})).json()
    names = {r["name"] for r in results if r["type"] == "table"}
    assert "leads" in names
    assert "ledger" not in names
    schema_names = {r["name"] for r in results if r["type"] == "schema"}
    assert "finance" not in schema_names
