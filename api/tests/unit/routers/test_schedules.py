import uuid
from datetime import UTC, datetime, timedelta

import pytest_asyncio
from conftest import seed_workspace
from httpx import AsyncClient

from api.models.query import Query, SavedQuery
from api.models.user import User
from api.models.workspace import Workspace
from api.services.auth import hash_password


@pytest_asyncio.fixture
async def user(db_session):
    u = User(email="sc@sched.local", password_hash=hash_password("pw"), name="Sch", role="user")
    db_session.add(u)
    await db_session.commit()
    await db_session.refresh(u)
    return u


@pytest_asyncio.fixture
async def authed_client(client: AsyncClient, user: User):
    await client.post("/auth/login", json={"email": "sc@sched.local", "password": "pw"})
    return client


@pytest_asyncio.fixture
async def workspace(db_session, user: User):
    ws, _catalog = await seed_workspace(db_session, user_id=user.id)
    return ws


@pytest_asyncio.fixture
async def saved_query(db_session, workspace: Workspace, user: User):
    sq = SavedQuery(workspace_id=workspace.id, name="nightly", sql="SELECT 1", created_by=user.id)
    db_session.add(sq)
    await db_session.commit()
    await db_session.refresh(sq)
    return sq


async def test_create_and_list_schedule(
    authed_client: AsyncClient, workspace: Workspace, saved_query: SavedQuery
):
    resp = await authed_client.post(
        f"/workspaces/{workspace.slug}/schedules",
        json={"saved_query_id": str(saved_query.id), "cron": "0 2 * * *"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["cron"] == "0 2 * * *"
    assert body["enabled"] is True
    assert body["next_run_at"] is not None  # computed on create

    listed = await authed_client.get(
        f"/workspaces/{workspace.slug}/schedules?saved_query_id={saved_query.id}"
    )
    assert listed.status_code == 200
    assert [s["id"] for s in listed.json()] == [body["id"]]


async def test_create_schedule_invalid_cron_422(
    authed_client: AsyncClient, workspace: Workspace, saved_query: SavedQuery
):
    resp = await authed_client.post(
        f"/workspaces/{workspace.slug}/schedules",
        json={"saved_query_id": str(saved_query.id), "cron": "not a cron"},
    )
    assert resp.status_code == 422


async def test_update_schedule_recomputes_next_run(
    authed_client: AsyncClient, workspace: Workspace, saved_query: SavedQuery
):
    created = (
        await authed_client.post(
            f"/workspaces/{workspace.slug}/schedules",
            json={"saved_query_id": str(saved_query.id), "cron": "0 2 * * *"},
        )
    ).json()

    # Disable -> next_run_at cleared.
    disabled = await authed_client.patch(
        f"/workspaces/{workspace.slug}/schedules/{created['id']}", json={"enabled": False}
    )
    assert disabled.status_code == 200
    assert disabled.json()["next_run_at"] is None

    # Bad cron on update is rejected.
    bad = await authed_client.patch(
        f"/workspaces/{workspace.slug}/schedules/{created['id']}", json={"cron": "bogus"}
    )
    assert bad.status_code == 422


async def test_delete_schedule(
    authed_client: AsyncClient, workspace: Workspace, saved_query: SavedQuery
):
    created = (
        await authed_client.post(
            f"/workspaces/{workspace.slug}/schedules",
            json={"saved_query_id": str(saved_query.id), "cron": "0 2 * * *"},
        )
    ).json()
    resp = await authed_client.delete(f"/workspaces/{workspace.slug}/schedules/{created['id']}")
    assert resp.status_code == 204
    listed = await authed_client.get(f"/workspaces/{workspace.slug}/schedules")
    assert listed.json() == []


async def test_list_schedule_runs_newest_first(
    authed_client: AsyncClient, workspace: Workspace, saved_query: SavedQuery, db_session
):
    created = (
        await authed_client.post(
            f"/workspaces/{workspace.slug}/schedules",
            json={"saved_query_id": str(saved_query.id), "cron": "0 2 * * *"},
        )
    ).json()
    schedule_id = created["id"]

    base = datetime(2026, 6, 29, 0, 0, tzinfo=UTC)
    for i in range(3):
        db_session.add(
            Query(
                workspace_id=workspace.id,
                sql="SELECT 1",
                status="done",
                origin="scheduled",
                schedule_id=uuid.UUID(schedule_id),
                started_at=base + timedelta(minutes=i),
            )
        )
    await db_session.commit()

    resp = await authed_client.get(f"/workspaces/{workspace.slug}/schedules/{schedule_id}/runs")
    assert resp.status_code == 200
    runs = resp.json()
    assert len(runs) == 3
    starts = [r["started_at"] for r in runs]
    assert starts == sorted(starts, reverse=True)  # newest first
    assert all(r["origin"] == "scheduled" for r in runs)


async def test_list_workspace_schedule_runs(
    authed_client: AsyncClient, workspace: Workspace, saved_query: SavedQuery, db_session
):
    created = (
        await authed_client.post(
            f"/workspaces/{workspace.slug}/schedules",
            json={"saved_query_id": str(saved_query.id), "cron": "0 2 * * *"},
        )
    ).json()
    schedule_id = created["id"]

    base = datetime(2026, 6, 29, 0, 0, tzinfo=UTC)
    # Two scheduled runs and one interactive run (schedule_id null).
    db_session.add_all(
        [
            Query(
                workspace_id=workspace.id,
                sql="SELECT 1",
                status="done",
                origin="scheduled",
                schedule_id=uuid.UUID(schedule_id),
                started_at=base,
            ),
            Query(
                workspace_id=workspace.id,
                sql="SELECT 1",
                status="failed",
                origin="scheduled",
                schedule_id=uuid.UUID(schedule_id),
                started_at=base + timedelta(minutes=5),
            ),
            Query(workspace_id=workspace.id, sql="SELECT 2", status="done", started_at=base),
        ]
    )
    await db_session.commit()

    resp = await authed_client.get(f"/workspaces/{workspace.slug}/schedule-runs")
    assert resp.status_code == 200
    runs = resp.json()
    # Only the two scheduled runs, newest first, each carrying its schedule_id.
    assert len(runs) == 2
    assert all(r["schedule_id"] == schedule_id for r in runs)
    starts = [r["started_at"] for r in runs]
    assert starts == sorted(starts, reverse=True)


async def test_non_member_forbidden(client: AsyncClient, db_session, workspace: Workspace):
    outsider = User(
        email="out@sched.local", password_hash=hash_password("pw"), name="Out", role="user"
    )
    db_session.add(outsider)
    await db_session.commit()
    await client.post("/auth/login", json={"email": "out@sched.local", "password": "pw"})
    resp = await client.get(f"/workspaces/{workspace.slug}/schedules")
    assert resp.status_code in (403, 404)
