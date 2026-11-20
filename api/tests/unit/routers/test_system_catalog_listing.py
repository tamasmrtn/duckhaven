"""`GET /workspaces/{ws}/system-catalog`: the system catalog's tables for the tree."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
import pytest_asyncio
from conftest import seed_workspace
from httpx import AsyncClient

from api.config import settings
from api.models.rbac import Role, RolePermission
from api.models.user import User
from api.models.workspace import WorkspaceMember
from api.services.auth import hash_password
from api.services.permissions import Permission
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
        ("info_schema", "catalogs"),
        ("info_schema", "schemata"),
        ("info_schema", "tables"),
        ("info_schema", "columns"),
        ("access", "audit"),
        ("access", "grants"),
        ("access", "users"),
        ("access", "workspaces"),
        ("access", "credentials"),
        ("access", "table_access"),
        ("query", "history"),
        ("lineage", "table_lineage"),
        ("lineage", "column_lineage"),
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


async def test_each_table_says_what_reading_it_takes(client, admin, workspace):
    resp = await (await _login(client, admin)).get(f"/workspaces/{workspace.slug}/system-catalog")

    assert {(t["schema_name"], t["name"]): t["permission"] for t in resp.json()["tables"]} == {
        # Every member's, each row narrowed to what they can see.
        ("info_schema", "catalogs"): None,
        ("info_schema", "schemata"): None,
        ("info_schema", "tables"): None,
        ("info_schema", "columns"): None,
        ("access", "audit"): "audit:read",
        ("access", "grants"): "audit:read",
        ("access", "users"): "audit:read",
        ("access", "workspaces"): "audit:read",
        ("access", "credentials"): "audit:read",
        ("access", "table_access"): "audit:read",
        ("query", "history"): "queries:admin",
        ("lineage", "table_lineage"): "audit:read",
        ("lineage", "column_lineage"): "audit:read",
    }


async def test_a_member_is_shown_only_the_tables_they_may_read(client, db_session, workspace):
    role = Role(name="auditor")
    db_session.add(role)
    await db_session.flush()
    db_session.add(RolePermission(role_id=role.id, permission=Permission.AUDIT_READ.value))
    auditor = await _user(db_session, "auditor@systree.local", "auditor")
    member = await _user(db_session, "member@systree.local", "user")
    for user in (auditor, member):
        db_session.add(WorkspaceMember(workspace_id=workspace.id, user_id=user.id, role="writer"))
    await db_session.commit()

    resp = await (await _login(client, auditor)).get(f"/workspaces/{workspace.slug}/system-catalog")
    assert [(t["schema_name"], t["name"]) for t in resp.json()["tables"]] == [
        ("info_schema", "catalogs"),
        ("info_schema", "schemata"),
        ("info_schema", "tables"),
        ("info_schema", "columns"),
        ("access", "audit"),
        ("access", "grants"),
        ("access", "users"),
        ("access", "workspaces"),
        ("access", "credentials"),
        ("access", "table_access"),
        ("lineage", "table_lineage"),
        ("lineage", "column_lineage"),
    ]
    resp = await (await _login(client, member)).get(f"/workspaces/{workspace.slug}/system-catalog")
    assert resp.status_code == 200
    assert [(t["schema_name"], t["name"]) for t in resp.json()["tables"]] == [
        ("info_schema", "catalogs"),
        ("info_schema", "schemata"),
        ("info_schema", "tables"),
        ("info_schema", "columns"),
    ]


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


# The mock API the web app is developed and tested against.
WEB_FIXTURE = Path(__file__).resolve().parents[4] / "web/src/mock/fixtures/systemCatalog.json"


async def test_the_web_mock_lists_what_an_admin_is_shown(client, admin, workspace):
    """Rewrite it with `UPDATE_SYSTEM_CATALOG_FIXTURE=1` after changing a table."""
    resp = await (await _login(client, admin)).get(f"/workspaces/{workspace.slug}/system-catalog")
    expected = json.dumps(resp.json(), indent=2, ensure_ascii=False) + "\n"

    if os.environ.get("UPDATE_SYSTEM_CATALOG_FIXTURE"):
        WEB_FIXTURE.write_text(expected)
    assert WEB_FIXTURE.read_text() == expected, (
        "web/src/mock/fixtures/systemCatalog.json is stale: rerun this test with "
        "UPDATE_SYSTEM_CATALOG_FIXTURE=1"
    )
