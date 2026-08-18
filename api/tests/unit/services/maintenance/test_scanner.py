from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from conftest import seed_workspace
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from api.models.agent import Agent
from api.models.catalog import Catalog
from api.models.maintenance import MaintenancePolicy, TableHealthSample
from api.models.query import Query
from api.models.user import User
from api.models.workspace import Workspace
from api.services.agent_registry import registry
from api.services.auth import hash_password
from api.services.maintenance import scanner as scanner_mod
from api.services.maintenance.scanner import (
    _due,
    _prune_old_samples,
    run_cycle,
    run_tick,
    scan_leadership,
)
from api.services.polaris import PolarisSnapshot


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


@pytest.fixture(autouse=True)
def _clear_enumeration_cache():
    # The scanner's TTL enumeration cache is module-level; a stale entry from a
    # previous test would leak across the (fresh per test) fake_polaris.
    scanner_mod._enumeration_cache.clear()
    yield
    scanner_mod._enumeration_cache.clear()


async def _seed(db, fake_polaris, *, connect_agent: bool = True) -> tuple[Workspace, FakeWS | None]:
    user = User(email="s@test.local", password_hash=hash_password("pw"), name="S", role="user")
    db.add(user)
    await db.flush()
    ws, catalog = await seed_workspace(db, user_id=user.id, slug="scan-ws", name="Scan")

    ws_obj = FakeWS()
    if connect_agent:
        agent = Agent(
            name="a", status="healthy", capabilities={"extensions": ["httpfs", "iceberg"]}
        )
        db.add(agent)
        await db.flush()
        registry.register(agent.id, ws_obj)  # type: ignore[arg-type]
    await db.commit()

    # Seed the catalog the scanner enumerates via Polaris (by polaris_name).
    pname = catalog.polaris_name
    await fake_polaris.create_catalog(pname, storage_type="S3", base_location="s3://b")
    await fake_polaris.create_schema(pname, "analytics")
    await fake_polaris.create_table(catalog=pname, schema="analytics", name="events", columns=[])
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


async def test_scan_leadership_granted_on_non_postgres(session_factory):
    """Without advisory locks (SQLite) leadership is always granted, so the
    single-process scanner behaves exactly as before."""
    async with scan_leadership(session_factory) as is_leader:
        assert is_leader is True


async def test_run_tick_standby_skips_cycle(session_factory, fake_polaris, monkeypatch):
    """A replica that loses leadership does not run a cycle."""
    import contextlib

    @contextlib.asynccontextmanager
    async def _no_leadership(_factory):
        yield False

    called = False

    async def _spy_run_cycle(*a, **k):
        nonlocal called
        called = True
        return {"status": "ran"}

    monkeypatch.setattr(scanner_mod, "scan_leadership", _no_leadership)
    monkeypatch.setattr(scanner_mod, "run_cycle", _spy_run_cycle)
    result = await run_tick(session_factory, fake_polaris)
    assert result == {"status": "standby"}
    assert called is False


async def _seed_prior_sample(db, fake_polaris, *, snapshot_id, scanned_at) -> Catalog:
    """Seed the workspace/table plus one prior health sample with a snapshot id."""
    ws, _ = await _seed(db, fake_polaris)
    catalog = (await db.execute(select(Catalog))).scalar_one()
    db.add(
        TableHealthSample(
            workspace_id=ws.id,
            catalog_id=catalog.id,
            schema_name="analytics",
            table_name="events",
            snapshot_id=snapshot_id,
            scanned_at=scanned_at,
        )
    )
    await db.commit()
    return catalog


def _set_snapshots(fake_polaris, catalog, snapshot_id: int) -> None:
    fake_polaris.snapshots[(catalog.polaris_name, "analytics", "events")] = [
        PolarisSnapshot(snapshot_id=snapshot_id, timestamp_ms=0, is_current=True)
    ]


async def test_filter_changed_skips_unchanged_table(session_factory, fake_polaris):
    async with session_factory() as db:
        catalog = await _seed_prior_sample(
            db, fake_polaris, snapshot_id=42, scanned_at=datetime.now(tz=UTC)
        )
    _set_snapshots(fake_polaris, catalog, 42)

    result = await run_cycle(session_factory, fake_polaris, force=True)
    assert result["status"] == "ran"
    assert result["dispatched"] == 0  # snapshot unchanged -> skipped


async def test_filter_changed_reprobes_changed_table(session_factory, fake_polaris):
    async with session_factory() as db:
        catalog = await _seed_prior_sample(
            db, fake_polaris, snapshot_id=42, scanned_at=datetime.now(tz=UTC)
        )
    _set_snapshots(fake_polaris, catalog, 43)

    result = await run_cycle(session_factory, fake_polaris, force=True)
    assert result["dispatched"] == 1  # snapshot changed -> re-probed


async def test_filter_changed_reprobes_stale_sample(session_factory, fake_polaris):
    # Even an unchanged snapshot is re-probed once the last sample is too old
    # (safety net so a silently-broken table doesn't go unobserved forever).
    async with session_factory() as db:
        catalog = await _seed_prior_sample(
            db,
            fake_polaris,
            snapshot_id=42,
            scanned_at=datetime.now(tz=UTC) - timedelta(days=8),
        )
    _set_snapshots(fake_polaris, catalog, 42)

    result = await run_cycle(session_factory, fake_polaris, force=True)
    assert result["dispatched"] == 1


async def test_prune_old_samples(session_factory, fake_polaris):
    async with session_factory() as db:
        ws, _ = await _seed(db, fake_polaris)
        catalog = (await db.execute(select(Catalog))).scalar_one()
        old = TableHealthSample(
            workspace_id=ws.id,
            catalog_id=catalog.id,
            schema_name="analytics",
            table_name="events",
            scanned_at=datetime.now(tz=UTC) - timedelta(days=91),
        )
        recent = TableHealthSample(
            workspace_id=ws.id,
            catalog_id=catalog.id,
            schema_name="analytics",
            table_name="events",
            scanned_at=datetime.now(tz=UTC) - timedelta(days=1),
        )
        db.add_all([old, recent])
        await db.commit()

        await _prune_old_samples(db, datetime.now(tz=UTC))
        remaining = (await db.execute(select(TableHealthSample))).scalars().all()
        assert len(remaining) == 1
        assert remaining[0].scanned_at == recent.scanned_at
