from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from api.models.agent import Agent
from api.models.maintenance import MaintenancePolicy
from api.models.query import Query
from api.models.storage_backend import StorageBackend
from api.models.user import User
from api.models.workspace import Workspace
from api.services.agent_registry import registry
from api.services.auth import hash_password
from api.services.maintenance.scanner import _due, run_cycle


class FakeWS:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_text(self, payload: str) -> None:
        self.sent.append(json.loads(payload))


@pytest_asyncio.fixture
def session_factory(db_engine):
    return async_sessionmaker(db_engine, expire_on_commit=False)


@pytest.fixture(autouse=True)
def _clear_registry():
    yield
    for cid in list(registry.connected_ids()):
        registry.unregister(uuid.UUID(cid))


async def _seed(db, fake_polaris, *, connect_agent: bool = True) -> tuple[Workspace, FakeWS | None]:
    user = User(email="s@test.local", password_hash=hash_password("pw"), name="S", role="user")
    db.add(user)
    await db.flush()
    sb = StorageBackend(kind="object_store", name="s", root_uri="", created_by=user.id)
    db.add(sb)
    await db.flush()
    ws = Workspace(slug="scan-ws", name="Scan", storage_backend_id=sb.id)
    db.add(ws)
    await db.flush()

    ws_obj = FakeWS()
    if connect_agent:
        agent = Agent(
            name="a", status="healthy", capabilities={"extensions": ["httpfs", "iceberg"]}
        )
        db.add(agent)
        await db.flush()
        registry.register(agent.id, ws_obj)  # type: ignore[arg-type]
    await db.commit()

    # Seed the catalog the scanner enumerates via Polaris.
    await fake_polaris.create_catalog(ws.slug, storage_type="S3", base_location="s3://b")
    await fake_polaris.create_schema(ws.slug, "analytics")
    await fake_polaris.create_table(catalog=ws.slug, schema="analytics", name="events", columns=[])
    return ws, (ws_obj if connect_agent else None)


def _policy(**over) -> MaintenancePolicy:
    base = dict(
        scan_enabled=True, scan_frequency="daily", last_scan_at=None, last_deep_scan_at=None
    )
    base.update(over)
    return MaintenancePolicy(preset="balanced", thresholds={}, max_tables_per_cycle=50, **base)


def test_due_logic():
    now = datetime(2026, 6, 16, tzinfo=UTC)
    assert _due(_policy(), now) is True  # never scanned
    assert _due(_policy(scan_enabled=False), now) is False
    assert _due(_policy(scan_frequency="off"), now) is False
    assert (
        _due(_policy(scan_frequency="daily", last_scan_at=now - timedelta(hours=2)), now) is False
    )
    assert _due(_policy(scan_frequency="daily", last_scan_at=now - timedelta(days=2)), now) is True
    assert (
        _due(_policy(scan_frequency="hourly", last_scan_at=now - timedelta(minutes=2)), now)
        is False
    )


async def test_run_cycle_dispatches_probe(session_factory, fake_polaris):
    async with session_factory() as db:
        ws, agent_ws = await _seed(db, fake_polaris)

    result = await run_cycle(session_factory, fake_polaris, force=True)
    assert result["status"] == "ran"
    assert result["dispatched"] == 1

    # A maintenance query was created and a dispatch frame was sent to the agent.
    async with session_factory() as db:
        q = (await db.execute(select(Query).where(Query.origin == "maintenance"))).scalar_one()
        assert q.sql == "SELECT 1"
    assert agent_ws is not None
    frame = agent_ws.sent[-1]
    assert frame["type"] == "dispatch_query"
    assert frame["payload"]["health_for"]["table"] == "events"
    assert frame["payload"]["health_for"]["include_orphans"] is True  # first cycle = deep


async def test_run_cycle_skips_when_not_due(session_factory, fake_polaris):
    async with session_factory() as db:
        await _seed(db, fake_polaris)
    # Running once sets last_scan_at; the next non-forced cycle is then not due.
    await run_cycle(session_factory, fake_polaris, force=True)
    result = await run_cycle(session_factory, fake_polaris, force=False)
    assert result["status"] == "skipped"


async def test_run_cycle_graceful_without_agent(session_factory, fake_polaris):
    async with session_factory() as db:
        await _seed(db, fake_polaris, connect_agent=False)
    result = await run_cycle(session_factory, fake_polaris, force=True)
    # No compatible agent connected -> nothing dispatched, no error.
    assert result["status"] == "ran"
    assert result["dispatched"] == 0


async def test_disabled_policy_short_circuits(session_factory, fake_polaris):
    async with session_factory() as db:
        await _seed(db, fake_polaris)
        db.add(
            MaintenancePolicy(
                scan_enabled=False,
                scan_frequency="daily",
                preset="balanced",
                thresholds={},
                max_tables_per_cycle=50,
            )
        )
        await db.commit()
    result = await run_cycle(session_factory, fake_polaris, force=False)
    assert result["status"] == "skipped"
