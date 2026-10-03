"""The result cache inside SQL sessions: the state a held connection is in decides
whether a statement may be answered from the cache, and it is tracked on the
session row as statements finish."""

from __future__ import annotations

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
from api.models.result_cache import ResultCacheEntry
from api.models.sql_session import SqlSession
from api.models.user import User
from api.services import query as query_service
from api.services.agent_registry import registry
from api.services.auth import hash_password
from api.services.polaris import PolarisTable, PolarisTableVersion
from api.services.result_cache import service as result_cache
from api.services.result_cache.session import reduce
from duckhaven_shared.protocol import Frame, FrameType

SQL = "SELECT label, count(*) AS n FROM events GROUP BY label ORDER BY label"
CONTEXT = {"catalog": "test_ws", "schema": "analytics", "timezone": "UTC"}

# --- The state reducer ------------------------------------------------------

CLEAN = {
    "catalog": "test_ws",
    "schema": "analytics",
    "timezone": "UTC",
    "in_txn": False,
    "tainted": False,
    "tainted_reason": None,
}


def test_a_transaction_opens_and_closes() -> None:
    opened = reduce(CLEAN, "BEGIN", succeeded=True, reported=None)
    assert opened["in_txn"]
    assert not reduce(opened, "COMMIT", succeeded=True, reported=None)["in_txn"]
    assert not reduce(opened, "ROLLBACK", succeeded=True, reported=None)["in_txn"]
    # A failed COMMIT rolls back: the transaction is over either way.
    assert not reduce(opened, "COMMIT", succeeded=False, reported=None)["in_txn"]


@pytest.mark.parametrize(
    ("sql", "reason"),
    [
        ("CREATE TEMP TABLE t AS SELECT 1", "temporary_object"),
        ("CREATE OR REPLACE TEMPORARY VIEW v AS SELECT 1", "temporary_object"),
        ("CREATE MACRO m(x) AS x", "temporary_object"),
        ("SET search_path = 'a,b'", "setting"),
        ("ATTACH 'x' AS y", "attach"),
        ("RESET search_path", "unparsed"),
    ],
)
def test_session_local_state_taints_for_good(sql: str, reason: str) -> None:
    state = reduce(CLEAN, sql, succeeded=True, reported=None)
    assert (state["tainted"], state["tainted_reason"]) == (True, reason)
    # Sticky: nothing later clears it.
    assert reduce(state, "COMMIT", succeeded=True, reported=CONTEXT)["tainted"]


@pytest.mark.parametrize(
    "sql",
    [
        "USE test_ws.other",
        "SET TimeZone = 'Europe/Budapest'",
        "SET schema = 'other'",
        "INSERT INTO events VALUES (1, 'a')",
        "CREATE TABLE test_ws.analytics.t AS SELECT 1",
        "SELECT 1",
    ],
)
def test_context_moves_and_catalog_writes_do_not_taint(sql: str) -> None:
    assert not reduce(CLEAN, sql, succeeded=True, reported=CONTEXT)["tainted"]


def test_the_reported_context_is_adopted() -> None:
    moved = {"catalog": "lake", "schema": "s", "timezone": "Asia/Tokyo"}
    state = reduce(CLEAN, "USE lake.s", succeeded=True, reported=moved)
    assert (state["catalog"], state["schema"], state["timezone"]) == ("lake", "s", "Asia/Tokyo")


def test_a_failed_script_taints_but_a_failed_read_does_not() -> None:
    assert reduce(CLEAN, "BEGIN; USE other; SELECT nope", succeeded=False, reported=None)["tainted"]
    assert not reduce(CLEAN, "SELECT * FROM missing", succeeded=False, reported=None)["tainted"]


# --- The statement route ------------------------------------------------------


def _parquet() -> bytes:
    fd, path = tempfile.mkstemp(suffix=".parquet")
    os.close(fd)
    try:
        conn = duckdb.connect()
        conn.execute(
            f"COPY (SELECT * FROM (VALUES ('a', 2), ('b', 1)) v(label, n)) TO '{path}' "
            "(FORMAT PARQUET)"
        )
        conn.close()
        with open(path, "rb") as f:
            return f.read()
    finally:
        os.unlink(path)


RESULT = _parquet()


@dataclass(frozen=True)
class Ref:
    id: uuid.UUID
    slug: str = ""


class _WS:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_text(self, text: str) -> None:
        self.sent.append(json.loads(text))

    def count(self, kind: FrameType) -> int:
        return sum(1 for f in self.sent if f["type"] == kind)


@pytest.fixture
def sessions(db_engine):
    return async_sessionmaker(db_engine, expire_on_commit=False)


@pytest.fixture(autouse=True)
def cache_on(monkeypatch, sessions):
    monkeypatch.setattr(settings, "result_cache_enabled", True)
    monkeypatch.setattr(settings, "sql_sessions_enabled", True)
    monkeypatch.setattr(settings, "sql_statement_wait_timeout_s", 0.0)
    monkeypatch.setattr(result_cache, "admission_session_factory", sessions)

    async def fake_proxy_rows(agent, query, *, row_offset=None, row_limit=None, token=None):
        return httpx.Response(200, content=RESULT)

    monkeypatch.setattr(query_service, "proxy_rows", fake_proxy_rows)


@pytest_asyncio.fixture
async def user(db_session):
    u = User(email="ss@cache.local", password_hash=hash_password("pw"), name="S", role="user")
    db_session.add(u)
    await db_session.commit()
    await db_session.refresh(u)
    return Ref(u.id)


@pytest_asyncio.fixture
async def authed_client(client: AsyncClient, user):
    await client.post("/auth/login", json={"email": "ss@cache.local", "password": "pw"})
    return client


@pytest_asyncio.fixture
async def setup(db_session, user, fake_polaris):
    """A workspace with `events`, a connected agent, and an open session on it."""
    ws, catalog = await seed_workspace(db_session, user_id=user.id)
    fake_polaris.tables[(catalog.polaris_name, "analytics", "events")] = PolarisTable(
        name="events", catalog_name=catalog.polaris_name, schema_name="analytics"
    )
    fake_polaris.versions[(catalog.polaris_name, "analytics", "events")] = PolarisTableVersion(
        table_uuid="u", snapshot_id=1, schema_id=0, metadata_location="m/1"
    )
    agent = Agent(
        name="a",
        status="healthy",
        capabilities={"duckdb_version": "1.5.5", "extensions": ["httpfs"], "timezone": "UTC"},
        result_host="agent.local",
        result_port=8001,
    )
    db_session.add(agent)
    await db_session.flush()
    session = SqlSession(
        workspace_id=ws.id,
        agent_id=agent.id,
        user_id=user.id,
        status="open",
        active_catalog=catalog.slug,
        staging_uri="/tmp/test/_staging/x/",
        runtime_id="1.5",
    )
    db_session.add(session)
    await db_session.commit()
    socket = _WS()
    registry.register(agent.id, socket)  # type: ignore[arg-type]
    yield Ref(ws.id, ws.slug), Ref(session.id), socket
    registry.unregister(agent.id)


async def _statement(client: AsyncClient, session: Ref, sql: str = SQL, **extra) -> dict:
    resp = await client.post(f"/sql/sessions/{session.id}/statements", json={"sql": sql, **extra})
    assert resp.status_code in (200, 202), resp.text
    return resp.json()


async def _done(sessions, fake_polaris, query_id: str, *, context=CONTEXT, status="done"):
    payload = {
        "query_id": query_id,
        "status": status,
        "row_count": 2,
        "duration_ms": 1500,
        "result_bytes": len(RESULT),
        "result_path": f"/r/{query_id}.parquet",
    }
    if context is not None:
        payload["session_context"] = context
    async with sessions() as db:
        await query_service.handle_agent_frame(
            db, Frame(type=FrameType.QUERY_DONE, payload=payload), fake_polaris
        )
    await result_cache.drain_admissions()


async def test_a_repeated_session_read_is_answered_without_running(
    authed_client, setup, sessions, fake_polaris
):
    _, session, socket = setup
    first = await _statement(authed_client, session)
    assert first["cache_status"] == "miss"
    await _done(sessions, fake_polaris, first["id"])

    second = await _statement(authed_client, session)
    assert (second["status"], second["cache_status"]) == ("done", "hit")
    assert second["session_id"] == str(session.id)
    assert second["first_page"]["rows"] == [{"label": "a", "n": 2}, {"label": "b", "n": 1}]
    assert socket.count(FrameType.EXEC_STATEMENT) == 1


async def test_a_session_shares_entries_with_one_shot_queries(
    authed_client, setup, sessions, fake_polaris
):
    """Same query, same catalog context: one entry, whichever path made it."""
    workspace, session, socket = setup
    async with sessions() as db:
        agent_id = (await db.get(SqlSession, session.id)).agent_id
    resp = await authed_client.post(
        f"/workspaces/{workspace.slug}/queries", json={"sql": SQL, "agent_id": str(agent_id)}
    )
    await _done(sessions, fake_polaris, resp.json()["id"], context=None)
    hit = await _statement(authed_client, session)
    assert hit["cache_status"] == "hit"
    assert socket.count(FrameType.EXEC_STATEMENT) == 0


async def test_reads_inside_a_transaction_always_run(authed_client, setup, sessions, fake_polaris):
    _, session, socket = setup
    first = await _statement(authed_client, session)
    await _done(sessions, fake_polaris, first["id"])
    begin = await _statement(authed_client, session, "BEGIN")
    await _done(sessions, fake_polaris, begin["id"])

    inside = await _statement(authed_client, session)
    assert (inside["cache_status"], inside["cache_detail"]) == ("ineligible", "in_transaction")
    await _done(sessions, fake_polaris, inside["id"])
    commit = await _statement(authed_client, session, "COMMIT")
    await _done(sessions, fake_polaris, commit["id"])
    assert (await _statement(authed_client, session))["cache_status"] == "hit"


async def test_a_temporary_table_ends_caching_for_the_session(
    authed_client, setup, sessions, fake_polaris
):
    _, session, _ = setup
    first = await _statement(authed_client, session)
    await _done(sessions, fake_polaris, first["id"])
    temp = await _statement(authed_client, session, "CREATE TEMP TABLE events AS SELECT 1 AS x")
    await _done(sessions, fake_polaris, temp["id"])
    shadowed = await _statement(authed_client, session)
    assert (shadowed["cache_status"], shadowed["cache_detail"]) == ("ineligible", "session_state")


async def test_a_use_keys_later_statements_in_the_new_context(
    authed_client, setup, sessions, fake_polaris
):
    _, session, _ = setup
    first = await _statement(authed_client, session)
    await _done(sessions, fake_polaris, first["id"])
    moved = await _statement(authed_client, session, "USE test_ws.other")
    await _done(sessions, fake_polaris, moved["id"], context={**CONTEXT, "schema": "other"})
    # `events` now means test_ws.other.events, which is not a table.
    again = await _statement(authed_client, session)
    assert (again["cache_status"], again["cache_detail"]) == ("ineligible", "not_a_table")


async def test_a_statement_still_running_holds_back_the_next(authed_client, setup):
    _, session, _ = setup
    await _statement(authed_client, session, "INSERT INTO events VALUES (1, 'a')")
    pending = await _statement(authed_client, session)
    assert (pending["cache_status"], pending["cache_detail"]) == ("bypass", "session_busy")


async def test_a_session_can_opt_out(authed_client, setup, sessions, fake_polaris):
    _, session, socket = setup
    first = await _statement(authed_client, session)
    await _done(sessions, fake_polaris, first["id"])
    forced = await _statement(authed_client, session, use_cache=False)
    assert forced["cache_status"] == "bypass"
    async with sessions() as db:
        (await db.get(SqlSession, session.id)).use_cache = False
        await db.commit()
    assert (await _statement(authed_client, session))["cache_detail"] == "opted_out"


async def test_a_session_opened_without_the_cache_records_it(authed_client, setup, sessions):
    workspace, _, _ = setup
    resp = await authed_client.post(
        f"/workspaces/{workspace.slug}/sql/sessions",
        json={"use_cache": False, "wait_timeout_s": 0, "on_wait_timeout": "continue"},
    )
    assert resp.status_code in (200, 201, 202), resp.text
    assert resp.json()["use_cache"] is False
    async with sessions() as db:
        assert (await db.get(SqlSession, uuid.UUID(resp.json()["id"]))).use_cache is False


async def test_only_unchanged_context_admits_a_session_result(
    authed_client, setup, sessions, fake_polaris
):
    """If the session moved while the read ran (it cannot, but a state we did not
    expect is still a state we cannot vouch for), the result is not kept."""
    _, session, _ = setup
    first = await _statement(authed_client, session)
    await _done(sessions, fake_polaris, first["id"], context={**CONTEXT, "timezone": "Asia/Tokyo"})
    async with sessions() as db:
        assert (await db.scalars(select(ResultCacheEntry))).all() == []
