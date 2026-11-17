"""Changes to who can reach a catalog, a table or an agent leave an audit event."""

from __future__ import annotations

import pytest
from audit_helpers import actions, audit_events
from conftest import seed_workspace
from httpx import AsyncClient

from api.models.agent import Agent
from api.models.catalog_grant import CatalogGrant
from api.models.user import User
from api.models.workspace import WorkspaceMember
from api.services.auth import hash_password


async def _user(db_session, email: str, role: str = "user") -> User:
    u = User(email=email, password_hash=hash_password("pw"), name=email, role=role)
    db_session.add(u)
    await db_session.commit()
    await db_session.refresh(u)
    return u


@pytest.fixture
async def owner(db_session) -> User:
    return await _user(db_session, "owner@audit.local")


@pytest.fixture
async def owner_client(client: AsyncClient, owner: User) -> AsyncClient:
    resp = await client.post("/auth/login", json={"email": owner.email, "password": "pw"})
    assert resp.status_code == 200
    return client


@pytest.fixture
async def workspace(db_session, owner):
    ws, cat = await seed_workspace(db_session, user_id=owner.id, slug="sales", catalog_slug="sales")
    member = await _user(db_session, "member@audit.local")
    db_session.add(WorkspaceMember(workspace_id=ws.id, user_id=member.id, role="reader"))
    await db_session.commit()
    return ws, cat, member


_GRANTS = "/workspaces/sales/catalogs/sales/grants"


async def test_granting_changing_and_revoking_data_access_is_recorded(
    owner_client, db_session, owner, workspace
):
    ws, cat, member = workspace
    body = {"user_id": str(member.id), "schema_name": "analytics", "table_name": "orders"}

    created = await owner_client.put(_GRANTS, json={**body, "tier": "reader"})
    assert created.status_code == 201, created.text
    assert (await owner_client.put(_GRANTS, json={**body, "tier": "writer"})).status_code == 200
    grant_id = created.json()["id"]
    assert (await owner_client.delete(f"{_GRANTS}/{grant_id}")).status_code == 204

    create, change, delete = await audit_events(db_session)
    assert [create.action, change.action, delete.action] == [
        "catalog_grant.create",
        "catalog_grant.tier_change",
        "catalog_grant.delete",
    ]
    for event in (create, change, delete):
        assert event.target_id == grant_id
        assert event.target_name == "sales.analytics.orders"
        assert (event.subject_email, event.workspace_slug) == (member.email, "sales")
        assert (event.actor_email, event.auth_method, event.self_change) == (
            owner.email,
            "session",
            False,
        )
    assert (create.before, create.after) == (None, {"tier": "reader"})
    assert (change.before, change.after) == ({"tier": "reader"}, {"tier": "writer"})
    assert (delete.before, delete.after) == ({"tier": "writer"}, None)
    assert delete.details["reason"] == "revoked"


async def test_an_unchanged_grant_or_mode_records_nothing(owner_client, db_session, workspace):
    _, _, member = workspace
    body = {"user_id": str(member.id), "tier": "reader"}
    await owner_client.put(_GRANTS, json=body)
    await owner_client.put(_GRANTS, json=body)
    mode = "/workspaces/sales/catalogs/sales/access-mode"
    await owner_client.patch(mode, json={"access_mode": "open"})

    assert await actions(db_session) == ["catalog_grant.create"]


async def test_a_refused_change_records_nothing(owner_client, db_session, workspace):
    outsider = await _user(db_session, "outsider@audit.local")
    resp = await owner_client.put(_GRANTS, json={"user_id": str(outsider.id), "tier": "reader"})

    assert resp.status_code == 422
    assert await audit_events(db_session) == []


async def test_changing_a_catalogs_access_mode_is_recorded(owner_client, db_session, workspace):
    resp = await owner_client.patch(
        "/workspaces/sales/catalogs/sales/access-mode", json={"access_mode": "scoped"}
    )
    assert resp.status_code == 200, resp.text

    (event,) = await audit_events(db_session)
    assert (event.action, event.target_name, event.workspace_slug) == (
        "catalog.access_mode_change",
        "sales",
        "sales",
    )
    assert (event.before, event.after) == ({"access_mode": "open"}, {"access_mode": "scoped"})


async def test_creating_a_scoped_catalog_records_it_and_the_creators_grant(
    owner_client, db_session, owner, workspace, fake_polaris
):
    resp = await owner_client.post(
        "/workspaces/sales/catalogs", json={"name": "finance", "access_mode": "scoped"}
    )
    assert resp.status_code == 201, resp.text

    create, attach, grant = await audit_events(db_session)
    assert [create.action, attach.action, grant.action] == [
        "catalog.create",
        "catalog.attach",
        "catalog_grant.create",
    ]
    assert create.target_name == attach.target_name == grant.target_name == "finance"
    assert attach.after == {"access_mode": "scoped"}
    assert (grant.subject_email, grant.self_change, grant.after) == (
        owner.email,
        True,
        {"tier": "writer"},
    )
    assert grant.details["reason"] == "creator_of_scoped_catalog"


async def test_attaching_and_detaching_a_catalog_is_recorded(
    owner_client, db_session, owner, workspace
):
    await seed_workspace(db_session, user_id=owner.id, slug="ops", catalog_slug="ops")

    assert (await owner_client.put("/workspaces/sales/catalogs/ops", json={})).status_code == 201
    # Already attached: an update, not a second attachment.
    assert (await owner_client.put("/workspaces/sales/catalogs/ops", json={})).status_code == 200
    assert (await owner_client.delete("/workspaces/sales/catalogs/ops")).status_code == 204

    attach, detach = await audit_events(db_session)
    assert (attach.action, attach.target_name, attach.workspace_slug) == (
        "catalog.attach",
        "ops",
        "sales",
    )
    assert attach.after == {"access_mode": "open"}
    assert (detach.action, detach.before) == ("catalog.detach", {"access_mode": "open"})


async def test_dropping_a_catalog_records_the_grants_it_takes(
    owner_client, db_session, owner, workspace
):
    ws, cat, member = workspace
    db_session.add(CatalogGrant(user_id=member.id, catalog_id=cat.id, tier="reader"))
    await db_session.commit()
    refused = await owner_client.delete(f"/catalogs/{cat.id}")
    assert refused.status_code == 409  # still attached: nothing happened, nothing recorded
    await owner_client.delete("/workspaces/sales/catalogs/sales")
    assert (await owner_client.delete(f"/catalogs/{cat.id}")).status_code == 204

    assert await actions(db_session) == ["catalog.detach", "catalog.drop", "catalog_grant.delete"]
    (removed,) = await audit_events(db_session, "catalog_grant.delete")
    assert (removed.subject_email, removed.target_name) == (member.email, "sales")
    assert removed.details["reason"] == "catalog_dropped"


async def test_dropping_a_table_records_the_grants_it_takes(
    owner_client, db_session, owner, workspace, fake_polaris
):
    ws, cat, member = workspace
    base = "/workspaces/sales/catalogs/sales/schemas"
    await owner_client.post(base, json={"name": "analytics"})
    columns = [{"name": "id", "type": "INTEGER", "nullable": True}]
    created = await owner_client.post(
        f"{base}/analytics/tables", json={"name": "orders", "columns": columns}
    )
    assert created.status_code == 201, created.text
    db_session.add(
        CatalogGrant(
            user_id=member.id,
            catalog_id=cat.id,
            schema_name="analytics",
            table_name="orders",
            tier="reader",
        )
    )
    await db_session.commit()

    assert (await owner_client.delete(f"{base}/analytics/tables/orders")).status_code == 204

    (removed,) = await audit_events(db_session, "catalog_grant.delete")
    assert (removed.subject_email, removed.target_name, removed.workspace_slug) == (
        member.email,
        "sales.analytics.orders",
        "sales",
    )
    assert removed.details["reason"] == "object_dropped"


async def test_agent_access_changes_are_recorded(client, db_session):
    admin = await _user(db_session, "admin@audit.local", role="admin")
    person = await _user(db_session, "analyst@audit.local")
    agent = Agent(name="etl", status="healthy", capabilities={})
    db_session.add(agent)
    await db_session.commit()
    await client.post("/auth/login", json={"email": admin.email, "password": "pw"})
    base = f"/admin/agents/{agent.id}"

    mode = await client.patch(f"{base}/access-mode", json={"access_mode": "restricted"})
    assert mode.status_code == 200, mode.text
    granted = await client.put(f"{base}/grants", json={"user_id": str(person.id), "tier": "use"})
    assert granted.status_code == 201, granted.text
    await client.put(f"{base}/grants", json={"user_id": str(person.id), "tier": "operate"})
    await client.delete(f"{base}/grants/{granted.json()['id']}")

    assert await actions(db_session) == [
        "agent.access_mode_change",
        "agent_grant.create",
        "agent_grant.tier_change",
        "agent_grant.delete",
    ]
    mode_event, *grant_events = await audit_events(db_session)
    assert (mode_event.target_name, mode_event.after) == ("etl", {"access_mode": "restricted"})
    assert {e.subject_email for e in grant_events} == {person.email}
    assert grant_events[1].before == {"tier": "use"}


async def test_reconciling_ducklake_roles_is_recorded(client, db_session):
    admin = await _user(db_session, "admin@audit.local", role="admin")
    await client.post("/auth/login", json={"email": admin.email, "password": "pw"})

    assert (await client.post("/admin/catalogs/ducklake/reconcile-roles")).status_code == 200

    (event,) = await audit_events(db_session)
    assert (event.action, event.category, event.details) == (
        "catalog.ducklake_roles_reconcile",
        "credential",
        {"catalogs": 0, "reconciled": 0},
    )


async def _ducklake_catalog(db_session, owner, ws):
    from api.models.catalog import KIND_DUCKLAKE, Catalog, WorkspaceCatalog
    from api.models.storage_backend import StorageBackend

    backend = StorageBackend(
        kind="object_store", name="lake-store", root_uri="/tmp/lake", created_by=owner.id
    )
    db_session.add(backend)
    await db_session.flush()
    cat = Catalog(
        slug="lake",
        name="Lake",
        kind=KIND_DUCKLAKE,
        metadata_schema="cat_lake",
        storage_backend_id=backend.id,
        created_by=owner.id,
    )
    db_session.add(cat)
    await db_session.flush()
    db_session.add(WorkspaceCatalog(workspace_id=ws.id, catalog_id=cat.id, attached_by=owner.id))
    await db_session.commit()
    return cat


async def test_rotating_a_ducklake_password_is_recorded_without_it(
    owner_client, db_session, owner, workspace, monkeypatch
):
    from api.services.catalog_backends.ducklake import DuckLakeCatalogBackend

    async def _ensure(self, catalog):  # noqa: ANN001 - no catalog database in unit tests
        return None

    monkeypatch.setattr(DuckLakeCatalogBackend, "ensure", _ensure)
    ws, _, _ = workspace
    cat = await _ducklake_catalog(db_session, owner, ws)

    resp = await owner_client.post(f"/catalogs/{cat.id}/ducklake/rotate-role")
    assert resp.status_code == 200, resp.text

    (event,) = await audit_events(db_session)
    assert (event.action, event.target_name, event.category) == (
        "catalog.ducklake_role_rotate",
        "lake",
        "credential",
    )
    assert (event.before, event.after, event.details) == (None, None, None)


async def test_starting_an_export_is_recorded(owner_client, db_session, owner, workspace):
    ws, cat, _ = workspace
    lake = await _ducklake_catalog(db_session, owner, ws)

    resp = await owner_client.post(
        f"/catalogs/{lake.id}/exports?workspace=sales",
        json={"target_name": "berg", "target_storage_backend_id": str(cat.storage_backend_id)},
    )
    assert resp.status_code == 202, resp.text

    (event,) = await audit_events(db_session)
    assert (event.action, event.target_name, event.workspace_slug) == (
        "catalog.export",
        "lake",
        "sales",
    )
    assert event.details["target_name"] == "berg"
