"""Running the maintenance a recommendation asks for.

Mostly the refusals: this dispatches a statement that rewrites or deletes data.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from conftest import seed_workspace
from fastapi import HTTPException

from api.models.catalog import KIND_DUCKLAKE, KIND_ICEBERG_POLARIS
from api.models.maintenance import MaintenanceRecommendation
from api.models.user import User
from api.services.auth import hash_password
from api.services.maintenance import apply as apply_service


async def _seed(db, *, kind=KIND_DUCKLAKE, rec_kind="compact_small_files", status="open"):
    user = User(
        email=f"{uuid.uuid4().hex[:8]}@t.local",
        password_hash=hash_password("pw"),
        name="A",
        role="admin",
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)

    slug = f"ws{uuid.uuid4().hex[:6]}"
    ws, catalog = await seed_workspace(db, user_id=user.id, slug=slug, catalog_slug=slug)
    if kind == KIND_DUCKLAKE:
        catalog.kind = KIND_DUCKLAKE
        catalog.polaris_name = None
        catalog.metadata_schema = f"cat_{slug}"
        await db.commit()

    rec = MaintenanceRecommendation(
        workspace_id=ws.id,
        catalog_id=catalog.id,
        schema_name="analytics",
        table_name="events",
        kind=rec_kind,
        severity="warning",
        confidence="high",
        rationale="fragmented",
        status=status,
    )
    db.add(rec)
    await db.commit()
    await db.refresh(rec)
    return rec, catalog, ws, user


async def _start(db, seeded):
    rec, catalog, ws, user = seeded
    return await apply_service.start_apply(
        db, recommendation=rec, catalog=catalog, workspace=ws, user=user
    )


@pytest.mark.asyncio
async def test_an_iceberg_catalog_is_refused(db_session):
    """DuckDB's iceberg extension has no maintenance verbs, so there is nothing
    to run however much the finding resembles a DuckLake one."""
    seeded = await _seed(db_session, kind=KIND_ICEBERG_POLARIS)
    with pytest.raises(HTTPException) as exc:
        await _start(db_session, seeded)
    assert exc.value.status_code == 422
    assert "cannot run maintenance" in str(exc.value.detail)


@pytest.mark.asyncio
async def test_a_kind_that_prescribes_nothing_is_refused(db_session):
    """`investigate_growth` says to go and look, which is not a statement."""
    seeded = await _seed(db_session, rec_kind="investigate_growth")
    with pytest.raises(HTTPException) as exc:
        await _start(db_session, seeded)
    assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_a_dismissed_recommendation_is_refused(db_session):
    """Someone judged it not worth doing; acting anyway would override them."""
    seeded = await _seed(db_session, status="dismissed")
    with pytest.raises(HTTPException) as exc:
        await _start(db_session, seeded)
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_a_second_apply_on_the_same_catalog_is_refused(db_session):
    """Per catalog rather than per table: expire_snapshots and cleanup_orphans
    act on every table, so two table-looking applies can still collide."""
    rec, catalog, ws, user = await _seed(db_session)
    other = MaintenanceRecommendation(
        workspace_id=ws.id,
        catalog_id=catalog.id,
        schema_name="analytics",
        table_name="other",
        kind="compact_small_files",
        severity="warning",
        confidence="high",
        rationale="x",
        status="open",
        apply_status="running",
        applied_at=datetime.now(tz=UTC),
    )
    db_session.add(other)
    await db_session.commit()

    with pytest.raises(HTTPException) as exc:
        await apply_service.start_apply(
            db_session, recommendation=rec, catalog=catalog, workspace=ws, user=user
        )
    assert exc.value.status_code == 409
    assert "already running" in str(exc.value.detail)


@pytest.mark.asyncio
async def test_no_connected_agent_is_a_503_not_a_silent_success(db_session):
    seeded = await _seed(db_session)
    with pytest.raises(HTTPException) as exc:
        await _start(db_session, seeded)
    assert exc.value.status_code == 503


@pytest.mark.asyncio
async def test_a_stale_apply_is_swept_so_the_catalog_is_not_blocked_forever(db_session):
    """An agent that never reports back would otherwise leave every later apply
    on that catalog refused as a conflict."""
    rec, *_ = await _seed(db_session)
    rec.apply_status = "running"
    rec.applied_at = datetime.now(tz=UTC) - timedelta(days=2)
    await db_session.commit()

    swept = await apply_service.sweep_stale_applies(db_session, datetime.now(tz=UTC))

    await db_session.refresh(rec)
    assert swept == 1
    assert rec.apply_status == "failed"
    assert "never reported back" in rec.apply_error


@pytest.mark.asyncio
async def test_a_recent_apply_is_left_alone_by_the_sweep(db_session):
    rec, *_ = await _seed(db_session)
    rec.apply_status = "running"
    rec.applied_at = datetime.now(tz=UTC)
    await db_session.commit()

    assert await apply_service.sweep_stale_applies(db_session, datetime.now(tz=UTC)) == 0
