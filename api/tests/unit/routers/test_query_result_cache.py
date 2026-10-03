"""The result cache on the one-shot query path: a read whose tables are unchanged
since an identical query ran is answered without dispatching anything.

Drives the real routes and the real QUERY_DONE handler. Polaris is the in-memory
fake (table versions are seeded per test), and the agent's result server is
replaced by a function serving Parquet built here.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import uuid
from dataclasses import dataclass

import duckdb
import httpx
import pytest
import pytest_asyncio
from conftest import seed_workspace
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from api.config import settings
from api.models.agent import Agent
from api.models.catalog import WorkspaceCatalog
from api.models.query import Query
from api.models.result_cache import ResultCacheEntry
from api.models.user import User
from api.models.workspace import Workspace
from api.services import query as query_service
from api.services.agent_registry import registry
from api.services.auth import hash_password
from api.services.polaris import PolarisSnapshot, PolarisTable, PolarisTableVersion
from api.services.result_cache import service as result_cache
from duckhaven_shared.protocol import Frame, FrameType

SQL = "SELECT label, count(*) AS n FROM events GROUP BY label ORDER BY label"


class MockWebSocket:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_text(self, text: str) -> None:
        self.sent.append(json.loads(text))

    def frames(self, kind: FrameType) -> list[dict]:
        return [f for f in self.sent if f["type"] == kind]


def _parquet(rows: list[tuple[str, int]]) -> bytes:
    fd, path = tempfile.mkstemp(suffix=".parquet")
    os.close(fd)
    try:
        conn = duckdb.connect()
        values = ", ".join(f"('{label}', {n})" for label, n in rows)
        conn.execute(
            f"COPY (SELECT * FROM (VALUES {values}) v(label, n)) TO '{path}' (FORMAT PARQUET)"
        )
        conn.close()
        with open(path, "rb") as f:
            return f.read()
    finally:
        os.unlink(path)


RESULT = _parquet([("a", 2), ("b", 1)])


@dataclass(frozen=True)
class Ref:
    """Plain identifiers, so a commit in another session never leaves a test
    holding an expired ORM object."""

    id: uuid.UUID
    slug: str = ""


@pytest.fixture
def sessions(db_engine):
    return async_sessionmaker(db_engine, expire_on_commit=False)


@pytest.fixture(autouse=True)
def cache_on(monkeypatch, sessions):
    monkeypatch.setattr(settings, "result_cache_enabled", True)
    monkeypatch.setattr(result_cache, "admission_session_factory", sessions)


@pytest.fixture(autouse=True)
def agent_results(monkeypatch):
    """Stand in for the agent's result server: every result file is `RESULT`."""
    served: list[uuid.UUID] = []

    async def fake_proxy_rows(agent, query, *, row_offset=None, row_limit=None, token=None):
        served.append(query.id)
        return httpx.Response(200, content=RESULT)

    monkeypatch.setattr(query_service, "proxy_rows", fake_proxy_rows)
    return served


@pytest_asyncio.fixture
async def user(db_session):
    u = User(email="c@cache.local", password_hash=hash_password("pw"), name="Cacher", role="user")
    db_session.add(u)
    await db_session.commit()
    await db_session.refresh(u)
    return u


@pytest_asyncio.fixture
async def authed_client(client: AsyncClient, user: User):
    await client.post("/auth/login", json={"email": "c@cache.local", "password": "pw"})
    return client


@pytest_asyncio.fixture
async def workspace(db_session, user: User, fake_polaris) -> Ref:
    ws, catalog = await seed_workspace(db_session, user_id=user.id)
    fake_polaris.tables[(catalog.polaris_name, "analytics", "events")] = PolarisTable(
        name="events", catalog_name=catalog.polaris_name, schema_name="analytics"
    )
    return Ref(ws.id, ws.slug)


def _set_version(fake_polaris, snapshot: int, *, schema: int = 0, location: str | None = None):
    fake_polaris.versions[("test-ws", "analytics", "events")] = PolarisTableVersion(
        table_uuid="u-events",
        snapshot_id=snapshot,
        schema_id=schema,
        metadata_location=location or f"s3://w/events/metadata/{snapshot:05d}-{schema}.json",
    )


@pytest_asyncio.fixture
async def agent(db_session):
    a = Agent(
        name="cache-agent",
        status="healthy",
        capabilities={"duckdb_version": "1.5.5", "extensions": ["httpfs"], "timezone": "UTC"},
        result_host="agent.local",
        result_port=8001,
    )
    db_session.add(a)
    await db_session.commit()
    await db_session.refresh(a)
    return Ref(a.id)


@pytest_asyncio.fixture
async def connected(agent: Ref):
    ws = MockWebSocket()
    registry.register(agent.id, ws)  # type: ignore[arg-type]
    yield agent, ws
    registry.unregister(agent.id)


async def _run(client: AsyncClient, workspace: Ref, agent: Ref, sql: str = SQL, **extra) -> dict:
    resp = await client.post(
        f"/workspaces/{workspace.slug}/queries",
        json={"sql": sql, "agent_id": str(agent.id), **extra},
    )
    assert resp.status_code == 202, resp.text
    return resp.json()


async def _finish(sessions, fake_polaris, query_id: str, *, result_bytes: int = len(RESULT)):
    """Deliver the agent's QUERY_DONE (on its own session, like the WebSocket
    handler) and wait for admission."""
    frame = Frame(
        type=FrameType.QUERY_DONE,
        payload={
            "query_id": query_id,
            "status": "done",
            "row_count": 2,
            "duration_ms": 4200,
            "result_bytes": result_bytes,
            "result_path": f"/r/{query_id}.parquet",
            "result_schema": [
                {"name": "label", "type": "VARCHAR"},
                {"name": "n", "type": "BIGINT"},
            ],
        },
    )
    async with sessions() as db:
        await query_service.handle_agent_frame(db, frame, fake_polaris)
    await result_cache.drain_admissions()


async def _entries(sessions) -> list[ResultCacheEntry]:
    async with sessions() as db:
        return list((await db.scalars(select(ResultCacheEntry))).all())


async def test_a_repeated_query_is_answered_without_running(
    authed_client, workspace, connected, sessions, fake_polaris, agent_results
):
    agent, ws = connected
    _set_version(fake_polaris, 1)
    first = await _run(authed_client, workspace, agent)
    assert first["status"] == "queued"
    assert first["cache_status"] == "miss"
    assert len(ws.frames(FrameType.DISPATCH_QUERY)) == 1

    await _finish(sessions, fake_polaris, first["id"])
    [entry] = await _entries(sessions)
    assert entry.storage == "inline"
    assert entry.inline_parquet == RESULT

    second = await _run(authed_client, workspace, agent)
    assert second["status"] == "done"
    assert second["cache_status"] == "hit"
    assert second["result_source_query_id"] == first["id"]
    assert second["agent_id"] is None
    assert second["row_count"] == 2
    assert second["column_schema"] == [
        {"name": "label", "type": "VARCHAR"},
        {"name": "n", "type": "BIGINT"},
    ]
    # Nothing was dispatched for the hit.
    assert len(ws.frames(FrameType.DISPATCH_QUERY)) == 1

    served_before = len(agent_results)
    rows = (await authed_client.get(f"/queries/{second['id']}/rows")).json()
    assert rows["rows"] == [{"label": "a", "n": 2}, {"label": "b", "n": 1}]
    assert rows["total"] == 2
    # Inline rows are served from Postgres; the agent is not asked.
    assert len(agent_results) == served_before


async def test_layout_and_comments_do_not_split_the_entry(
    authed_client, workspace, connected, sessions, fake_polaris
):
    agent, _ = connected
    _set_version(fake_polaris, 1)
    first = await _run(authed_client, workspace, agent)
    await _finish(sessions, fake_polaris, first["id"])
    hit = await _run(
        authed_client,
        workspace,
        agent,
        sql="select label, COUNT(*) as n -- per label\nfrom events group by label order by label",
    )
    assert hit["cache_status"] == "hit"


async def test_the_hit_survives_the_agent_going_away(
    authed_client, workspace, connected, sessions, fake_polaris
):
    agent, _ = connected
    _set_version(fake_polaris, 1)
    first = await _run(authed_client, workspace, agent)
    await _finish(sessions, fake_polaris, first["id"])
    registry.unregister(agent.id)
    hit = await _run(authed_client, workspace, agent)
    assert hit["cache_status"] == "hit"


async def test_a_new_snapshot_is_a_miss_and_drops_the_entry(
    authed_client, workspace, connected, sessions, fake_polaris
):
    agent, ws = connected
    _set_version(fake_polaris, 1)
    first = await _run(authed_client, workspace, agent)
    await _finish(sessions, fake_polaris, first["id"])

    _set_version(fake_polaris, 2)
    fake_polaris.snapshots[("test-ws", "analytics", "events")] = [
        PolarisSnapshot(snapshot_id=2, parent_snapshot_id=1, timestamp_ms=2, operation="append"),
        PolarisSnapshot(snapshot_id=1, parent_snapshot_id=None, timestamp_ms=1, operation="append"),
    ]
    again = await _run(authed_client, workspace, agent)
    assert again["cache_status"] == "miss"
    assert len(ws.frames(FrameType.DISPATCH_QUERY)) == 2
    assert await _entries(sessions) == []


async def test_a_schema_change_without_a_snapshot_is_a_miss(
    authed_client, workspace, connected, sessions, fake_polaris
):
    agent, _ = connected
    _set_version(fake_polaris, 1)
    first = await _run(authed_client, workspace, agent)
    await _finish(sessions, fake_polaris, first["id"])
    _set_version(fake_polaris, 1, schema=1)
    assert (await _run(authed_client, workspace, agent))["cache_status"] == "miss"


async def test_compaction_keeps_serving_and_moves_the_entry_forward(
    authed_client, workspace, connected, sessions, fake_polaris
):
    agent, _ = connected
    _set_version(fake_polaris, 1)
    first = await _run(authed_client, workspace, agent)
    await _finish(sessions, fake_polaris, first["id"])

    _set_version(fake_polaris, 2)
    fake_polaris.snapshots[("test-ws", "analytics", "events")] = [
        PolarisSnapshot(snapshot_id=2, parent_snapshot_id=1, timestamp_ms=2, operation="replace"),
        PolarisSnapshot(snapshot_id=1, parent_snapshot_id=None, timestamp_ms=1, operation="append"),
    ]
    hit = await _run(authed_client, workspace, agent)
    assert hit["cache_status"] == "hit"
    [entry] = await _entries(sessions)
    assert entry.tables[0]["content_id"] == "u-events:2:0"


async def test_a_commit_while_running_keeps_the_result_out(
    authed_client, workspace, connected, sessions, fake_polaris
):
    agent, _ = connected
    _set_version(fake_polaris, 1)
    first = await _run(authed_client, workspace, agent)
    _set_version(fake_polaris, 2)  # someone wrote while the query ran
    await _finish(sessions, fake_polaris, first["id"])
    assert await _entries(sessions) == []
    async with sessions() as db:
        row = await db.get(Query, uuid.UUID(first["id"]))
    assert row.cache_detail == "changed_during_run"


async def test_an_agent_that_does_not_report_its_time_zone_is_never_cached(
    authed_client, workspace, connected, sessions, fake_polaris
):
    agent, _ = connected
    async with sessions() as db:
        row = await db.get(Agent, agent.id)
        row.capabilities = {"duckdb_version": "1.5.5", "extensions": ["httpfs"]}
        await db.commit()
    _set_version(fake_polaris, 1)
    first = await _run(authed_client, workspace, agent)
    await _finish(sessions, fake_polaris, first["id"])
    assert await _entries(sessions) == []


async def test_a_revoked_grant_is_refused_even_with_an_entry(
    authed_client, workspace, connected, sessions, fake_polaris
):
    agent, ws = connected
    _set_version(fake_polaris, 1)
    first = await _run(authed_client, workspace, agent)
    await _finish(sessions, fake_polaris, first["id"])

    async with sessions() as db:
        link = await db.scalar(
            select(WorkspaceCatalog).where(WorkspaceCatalog.workspace_id == workspace.id)
        )
        link.access_mode = "scoped"  # and no grant for this user
        await db.commit()
    resp = await authed_client.post(
        f"/workspaces/{workspace.slug}/queries", json={"sql": SQL, "agent_id": str(agent.id)}
    )
    assert resp.status_code == 403
    assert resp.json()["error"] == "grant_denied"


@pytest.mark.parametrize(
    ("setup", "status", "detail"),
    [
        ("opt_out", "bypass", "opted_out"),
        ("workspace_off", "bypass", "workspace_disabled"),
        ("volatile", "ineligible", "volatile_function"),
        ("view", "ineligible", "not_a_table"),
    ],
)
async def test_runs_the_cache_cannot_answer_still_run(
    authed_client, workspace, connected, sessions, fake_polaris, setup, status, detail
):
    agent, ws = connected
    _set_version(fake_polaris, 1)
    sql, extra = SQL, {}
    if setup == "opt_out":
        extra = {"use_cache": False}
    elif setup == "workspace_off":
        async with sessions() as db:
            (await db.get(Workspace, workspace.id)).result_cache_enabled = False
            await db.commit()
    elif setup == "volatile":
        sql = "SELECT now()"
    elif setup == "view":
        sql = "SELECT * FROM a_view"
    run = await _run(authed_client, workspace, agent, sql=sql, **extra)
    assert (run["cache_status"], run["cache_detail"]) == (status, detail)
    assert run["status"] == "queued"
    assert len(ws.frames(FrameType.DISPATCH_QUERY)) == 1


async def test_re_running_without_the_cache_refreshes_it(
    authed_client, workspace, connected, sessions, fake_polaris
):
    agent, ws = connected
    _set_version(fake_polaris, 1)
    first = await _run(authed_client, workspace, agent)
    await _finish(sessions, fake_polaris, first["id"])
    forced = await _run(authed_client, workspace, agent, use_cache=False)
    assert forced["cache_status"] == "bypass"
    assert len(ws.frames(FrameType.DISPATCH_QUERY)) == 2


async def test_a_slow_catalog_bypasses_instead_of_waiting(
    authed_client, workspace, connected, fake_polaris, monkeypatch
):
    agent, ws = connected
    monkeypatch.setattr(settings, "result_cache_lookup_timeout_s", 0.01)

    async def slow(*_args, **_kwargs):
        await asyncio.sleep(1)

    monkeypatch.setattr(fake_polaris, "load_table_version", slow)
    run = await _run(authed_client, workspace, agent)
    assert (run["cache_status"], run["cache_detail"]) == ("bypass", "lookup_timeout")
    assert len(ws.frames(FrameType.DISPATCH_QUERY)) == 1


async def test_a_large_result_stays_on_the_agent(
    authed_client, workspace, connected, sessions, fake_polaris, monkeypatch, agent_results
):
    agent, ws = connected
    monkeypatch.setattr(settings, "result_cache_inline_max_bytes", 10)
    _set_version(fake_polaris, 1)
    first = await _run(authed_client, workspace, agent)
    await _finish(sessions, fake_polaris, first["id"])
    [entry] = await _entries(sessions)
    assert entry.storage == "agent"
    assert entry.inline_parquet is None
    [retain] = ws.frames(FrameType.RETAIN_RESULT)
    assert retain["payload"]["query_id"] == first["id"]

    async def available(_db, _entry):
        return True

    monkeypatch.setattr(result_cache, "_agent_result_available", available)
    hit = await _run(authed_client, workspace, agent)
    assert hit["cache_status"] == "hit"
    rows = (await authed_client.get(f"/queries/{hit['id']}/rows")).json()
    assert rows["total"] == 2
    # Read from the agent that produced it, addressed by the source run's id.
    assert agent_results[-1] == uuid.UUID(first["id"])


async def test_an_agent_result_that_is_gone_is_a_miss(
    authed_client, workspace, connected, sessions, fake_polaris, monkeypatch
):
    agent, ws = connected
    monkeypatch.setattr(settings, "result_cache_inline_max_bytes", 10)
    _set_version(fake_polaris, 1)
    first = await _run(authed_client, workspace, agent)
    await _finish(sessions, fake_polaris, first["id"])

    async def gone(_db, _entry):
        return False

    monkeypatch.setattr(result_cache, "_agent_result_available", gone)
    again = await _run(authed_client, workspace, agent)
    assert (again["cache_status"], again["cache_detail"]) == ("miss", "result_gone")
    assert await _entries(sessions) == []


async def test_a_hit_reports_the_source_runs_profile(
    authed_client, workspace, connected, sessions, fake_polaris
):
    agent, _ = connected
    _set_version(fake_polaris, 1)
    first = await _run(authed_client, workspace, agent)
    await _finish(sessions, fake_polaris, first["id"])
    async with sessions() as db:
        source = await db.get(Query, uuid.UUID(first["id"]))
        source.profile = {"summary": {"latency_ms": 4200}}
        await db.commit()
    hit = await _run(authed_client, workspace, agent)
    profile = (await authed_client.get(f"/queries/{hit['id']}/profile")).json()
    assert profile == {"summary": {"latency_ms": 4200}}


async def test_the_workspace_toggle_is_owner_settable(authed_client, workspace, sessions):
    resp = await authed_client.patch(
        f"/workspaces/{workspace.slug}", json={"result_cache_enabled": False}
    )
    assert resp.status_code == 200
    assert resp.json()["result_cache_enabled"] is False
    async with sessions() as db:
        assert (await db.get(Workspace, workspace.id)).result_cache_enabled is False


async def test_hits_are_recorded_in_history_with_their_source(
    authed_client, workspace, connected, sessions, fake_polaris
):
    agent, _ = connected
    _set_version(fake_polaris, 1)
    first = await _run(authed_client, workspace, agent)
    await _finish(sessions, fake_polaris, first["id"])
    hit = await _run(authed_client, workspace, agent)
    history = (await authed_client.get(f"/workspaces/{workspace.slug}/queries")).json()["items"]
    by_id = {q["id"]: q for q in history}
    assert by_id[hit["id"]]["cache_status"] == "hit"
    assert by_id[hit["id"]]["result_source_query_id"] == first["id"]
