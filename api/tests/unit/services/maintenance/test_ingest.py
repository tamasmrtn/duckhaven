from __future__ import annotations

import pytest
from sqlalchemy import select

from api.models.maintenance import MaintenanceRecommendation, TableHealthSample
from api.models.query import Query
from api.models.storage_backend import StorageBackend
from api.models.user import User
from api.models.workspace import Workspace
from api.services.auth import hash_password
from api.services.maintenance.ingest import record_health_sample

MIB = 1024 * 1024


async def _workspace(db) -> Workspace:
    user = User(email="m@test.local", password_hash=hash_password("pw"), name="M", role="user")
    db.add(user)
    await db.flush()
    sb = StorageBackend(kind="object_store", name="s", root_uri="", created_by=user.id)
    db.add(sb)
    await db.flush()
    ws = Workspace(slug="mws", name="MWS", storage_backend_id=sb.id)
    db.add(ws)
    await db.commit()
    await db.refresh(ws)
    return ws


async def _query(db, ws) -> Query:
    q = Query(workspace_id=ws.id, sql="SELECT 1", status="done", origin="maintenance")
    db.add(q)
    await db.commit()
    await db.refresh(q)
    return q


def _unhealthy(**over):
    base = {
        "schema": "analytics",
        "table": "events",
        "small_file_ratio": 0.85,
        "data_file_count": 200,
        "snapshot_count": 5,
        "manifest_count": 3,
        "total_data_bytes": 100 * MIB,
        "orphan_bytes": 0,
    }
    base.update(over)
    return base


@pytest.fixture(autouse=True)
def _clear_registry():
    from api.services.agent_registry import registry

    yield
    import uuid

    for cid in list(registry.connected_ids()):
        registry.unregister(uuid.UUID(cid))


async def test_records_sample_with_score_and_factors(db_session):
    ws = await _workspace(db_session)
    q = await _query(db_session, ws)
    await record_health_sample(db_session, q, _unhealthy())

    sample = (await db_session.execute(select(TableHealthSample))).scalar_one()
    assert sample.table_name == "events"
    assert sample.score is not None and sample.score < 70  # 85% small files drags it down
    assert "fragmentation" in sample.factors


async def test_creates_recommendation(db_session):
    ws = await _workspace(db_session)
    q = await _query(db_session, ws)
    await record_health_sample(db_session, q, _unhealthy())

    rec = (
        await db_session.execute(
            select(MaintenanceRecommendation).where(
                MaintenanceRecommendation.kind == "compact_small_files"
            )
        )
    ).scalar_one()
    assert rec.status == "open"
    assert rec.severity == "critical"


async def test_recommendation_auto_resolves_when_condition_clears(db_session):
    ws = await _workspace(db_session)
    q = await _query(db_session, ws)
    await record_health_sample(db_session, q, _unhealthy())
    # Next scan: table is now healthy -> the open recommendation is resolved.
    await record_health_sample(db_session, q, _unhealthy(small_file_ratio=0.02))

    rec = (
        await db_session.execute(
            select(MaintenanceRecommendation).where(
                MaintenanceRecommendation.kind == "compact_small_files"
            )
        )
    ).scalar_one()
    assert rec.status == "resolved"
    assert rec.resolved_at is not None


async def test_dismissed_recommendation_stays_until_worse(db_session):
    ws = await _workspace(db_session)
    q = await _query(db_session, ws)
    await record_health_sample(db_session, q, _unhealthy(small_file_ratio=0.4))  # warning

    rec = (
        await db_session.execute(
            select(MaintenanceRecommendation).where(
                MaintenanceRecommendation.kind == "compact_small_files"
            )
        )
    ).scalar_one()
    rec.status = "dismissed"
    await db_session.commit()

    # Same severity -> stays dismissed.
    await record_health_sample(db_session, q, _unhealthy(small_file_ratio=0.4))
    await db_session.refresh(rec)
    assert rec.status == "dismissed"

    # Worsens to critical -> reopens.
    await record_health_sample(db_session, q, _unhealthy(small_file_ratio=0.85))
    await db_session.refresh(rec)
    assert rec.status == "open"
    assert rec.severity == "critical"


async def test_ignores_sample_without_table(db_session):
    ws = await _workspace(db_session)
    q = await _query(db_session, ws)
    await record_health_sample(db_session, q, {"schema": "analytics"})
    assert (await db_session.execute(select(TableHealthSample))).first() is None
