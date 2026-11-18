"""`GET /workspaces/{ws}/system-catalog`: the system catalog's tables for the tree."""

from __future__ import annotations

import pytest
import pytest_asyncio
from conftest import seed_workspace
from httpx import AsyncClient

from api.config import settings
from api.models.user import User
from api.models.workspace import WorkspaceMember
from api.services.auth import hash_password
from api.services.system_catalog import store


async def _user(db, email: str, role: str) -> User:
    u = User(email=email, password_hash=hash_password("pw"), name=email, role=role)
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


async def _login(client: AsyncClient, user: User) -> AsyncClient:
    await client.post("/auth/login", json={"email": user.email, "password": "pw"})
    return client


@pytest_asyncio.fixture
async def admin(db_session) -> User:
    return await _user(db_session, "admin@systree.local", "admin")


@pytest_asyncio.fixture
async def workspace(db_session, admin):
    ws, _cat = await seed_workspace(db_session, user_id=admin.id, catalog_slug="sales")
    return ws


@pytest.fixture(autouse=True)
def enabled(monkeypatch):
    monkeypatch.setattr(settings, "system_catalog_enabled", True)
    monkeypatch.setattr(store, "is_available", lambda: True)


def _columns(body: dict, schema: str, table: str) -> dict[str, str]:
    (found,) = [t for t in body["tables"] if (t["schema_name"], t["name"]) == (schema, table)]
    return {c["name"]: c["type"] for c in found["columns"]}


async def test_an_admin_gets_its_tables_with_the_types_duckdb_reports(client, admin, workspace):
    resp = await (await _login(client, admin)).get(f"/workspaces/{workspace.slug}/system-catalog")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["catalog"] == "duckhaven"
    assert body["available"] is True
    assert [(t["schema_name"], t["name"]) for t in body["tables"]] == [
        ("access", "audit"),
        ("access", "grants"),
        ("query", "history"),
    ]
    # As an agent's DESCRIBE showed them, JSONB read as text included.
    audit = _columns(body, "access", "audit")
    assert audit["id"] == "UUID"
    assert audit["event_time"] == "TIMESTAMP WITH TIME ZONE"
    assert audit["event_version"] == "INTEGER"
    assert audit["self_change"] == "BOOLEAN"
    assert audit["before"] == "VARCHAR"
    assert _columns(body, "query", "history")["row_count"] == "BIGINT"
    assert list(_columns(body, "access", "grants")) == [c.name for c in store.GRANTS.columns]


async def test_it_says_when_its_database_cannot_be_reached(client, admin, workspace, monkeypatch):
    monkeypatch.setattr(store, "is_available", lambda: False)

    resp = await (await _login(client, admin)).get(f"/workspaces/{workspace.slug}/system-catalog")

    assert resp.status_code == 200
    assert resp.json()["available"] is False


async def test_a_member_without_the_permissions_is_refused(client, db_session, workspace):
    member = await _user(db_session, "member@systree.local", "user")
    db_session.add(WorkspaceMember(workspace_id=workspace.id, user_id=member.id, role="writer"))
    await db_session.commit()

    resp = await (await _login(client, member)).get(f"/workspaces/{workspace.slug}/system-catalog")

    assert resp.status_code == 403


async def test_an_admin_outside_the_workspace_is_refused(client, db_session, workspace):
    other = await _user(db_session, "other-admin@systree.local", "admin")

    resp = await (await _login(client, other)).get(f"/workspaces/{workspace.slug}/system-catalog")

    assert resp.status_code == 403


async def test_it_is_absent_when_turned_off(client, admin, workspace, monkeypatch):
    monkeypatch.setattr(settings, "system_catalog_enabled", False)

    resp = await (await _login(client, admin)).get(f"/workspaces/{workspace.slug}/system-catalog")

    assert resp.status_code == 404


async def test_it_is_absent_where_a_workspace_catalog_has_its_name(client, db_session, admin):
    ws, _cat = await seed_workspace(
        db_session, user_id=admin.id, slug="legacy", catalog_slug="duckhaven"
    )

    resp = await (await _login(client, admin)).get(f"/workspaces/{ws.slug}/system-catalog")

    assert resp.status_code == 404


async def test_an_unknown_workspace_is_not_found(client, admin):
    resp = await (await _login(client, admin)).get("/workspaces/nope/system-catalog")

    assert resp.status_code == 404
