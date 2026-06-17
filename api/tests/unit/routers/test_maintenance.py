from __future__ import annotations

import pytest
from httpx import AsyncClient

from api.models.maintenance import MaintenanceRecommendation, TableHealthSample
from api.models.storage_backend import StorageBackend
from api.models.user import User
from api.models.workspace import Workspace, WorkspaceMember
from api.services.auth import hash_password

MIB = 1024 * 1024


@pytest.fixture
async def user(db_session) -> User:
    u = User(email="u@test.local", password_hash=hash_password("pw"), name="U", role="user")
    db_session.add(u)
    await db_session.commit()
    await db_session.refresh(u)
    return u


@pytest.fixture
async def workspace(db_session, user: User) -> Workspace:
    sb = StorageBackend(kind="object_store", name="s", root_uri="", created_by=user.id)
    db_session.add(sb)
    await db_session.flush()
    ws = Workspace(slug="hw", name="HW", storage_backend_id=sb.id)
    db_session.add(ws)
    await db_session.flush()
    db_session.add(WorkspaceMember(workspace_id=ws.id, user_id=user.id, role="writer"))
    await db_session.commit()
    await db_session.refresh(ws)
    return ws


@pytest.fixture
async def auth_client(client: AsyncClient, user: User) -> AsyncClient:
    await client.post("/auth/login", json={"email": "u@test.local", "password": "pw"})
    return client


async def _seed_sample(db, ws, *, table="events", score=60, bytes_=100 * MIB):
    db.add(
        TableHealthSample(
            workspace_id=ws.id,
            schema_name="analytics",
            table_name=table,
            score=score,
            total_data_bytes=bytes_,
            small_file_ratio=0.4,
            factors={"fragmentation": {"score": 50, "weight": 35, "detail": "x"}},
        )
    )
    await db.commit()


async def test_deployment_health_rolls_up(auth_client, workspace, db_session):
    await _seed_sample(db_session, workspace, table="a", score=40, bytes_=900 * MIB)
    await _seed_sample(db_session, workspace, table="b", score=100, bytes_=100 * MIB)
    resp = await auth_client.get("/maintenance/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["summary"]["table_count"] == 2
    assert data["summary"]["attention_count"] == 1
    assert len(data["workspaces"]) == 1
    # data-byte weighted -> closer to the big unhealthy table's 40 than to 100.
    assert data["summary"]["score"] < 60


async def test_workspace_health_lists_tables_worst_first(auth_client, workspace, db_session):
    await _seed_sample(db_session, workspace, table="healthy", score=95)
    await _seed_sample(db_session, workspace, table="bad", score=30)
    resp = await auth_client.get(f"/workspaces/{workspace.slug}/health")
    assert resp.status_code == 200
    data = resp.json()
    assert [t["table_name"] for t in data["tables"]] == ["bad", "healthy"]
    assert data["tables"][0]["band"] == "attention"
    assert len(data["namespaces"]) == 1


async def test_table_health_detail_includes_history_and_recs(auth_client, workspace, db_session):
    await _seed_sample(db_session, workspace, table="events", score=55)
    db_session.add(
        MaintenanceRecommendation(
            workspace_id=workspace.id,
            schema_name="analytics",
            table_name="events",
            kind="compact_small_files",
            severity="warning",
            confidence="high",
            rationale="40% small files",
            status="open",
        )
    )
    await db_session.commit()

    resp = await auth_client.get(
        f"/workspaces/{workspace.slug}/schemas/analytics/tables/events/health"
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["table"]["table_name"] == "events"
    assert len(data["history"]) == 1
    assert data["recommendations"][0]["kind"] == "compact_small_files"


async def test_table_health_404_without_data(auth_client, workspace):
    resp = await auth_client.get(
        f"/workspaces/{workspace.slug}/schemas/analytics/tables/ghost/health"
    )
    assert resp.status_code == 404


async def test_list_and_dismiss_recommendation(auth_client, workspace, db_session):
    rec = MaintenanceRecommendation(
        workspace_id=workspace.id,
        schema_name="analytics",
        table_name="events",
        kind="expire_snapshots",
        severity="warning",
        confidence="high",
        rationale="too many snapshots",
        status="open",
    )
    db_session.add(rec)
    await db_session.commit()
    await db_session.refresh(rec)

    listing = await auth_client.get("/maintenance/recommendations")
    assert listing.status_code == 200
    assert len(listing.json()) == 1

    dismiss = await auth_client.post(f"/maintenance/recommendations/{rec.id}/dismiss")
    assert dismiss.status_code == 200
    assert dismiss.json()["status"] == "dismissed"

    # No longer in the default (open) feed.
    again = await auth_client.get("/maintenance/recommendations")
    assert again.json() == []


async def test_health_requires_auth(client: AsyncClient):
    resp = await client.get("/maintenance/health")
    assert resp.status_code == 401


async def test_workspace_health_non_member_forbidden(client, workspace, db_session):
    other = User(email="o@test.local", password_hash=hash_password("pw"), name="O", role="user")
    db_session.add(other)
    await db_session.commit()
    await client.post("/auth/login", json={"email": "o@test.local", "password": "pw"})
    resp = await client.get(f"/workspaces/{workspace.slug}/health")
    assert resp.status_code == 403
