"""GET /queries/{query_id}/result: the whole result as Parquet or as an Arrow stream."""

from __future__ import annotations

import glob
import os
import tempfile
from decimal import Decimal

import duckdb
import httpx
import pyarrow as pa
import pytest
import pytest_asyncio
from conftest import seed_workspace
from httpx import AsyncClient
from sqlalchemy import update

from api.models.agent import Agent
from api.models.catalog import WorkspaceCatalog
from api.models.query import Query
from api.models.user import User
from api.services import query as query_service
from api.services import result_stream
from api.services.auth import hash_password

# One of each type that is awkward somewhere: exact numbers JSON would round, the
# HUGEINT the agent writes as DECIMAL(38,0), and the extension and nested types.
MATRIX_SQL = """
SELECT 123456789012345678.1234567891::DECIMAL(38,10) AS dec,
       12345678901234567890123::DECIMAL(38,0) AS wide,
       9007199254740993::BIGINT AS big,
       '00000000-0000-0000-0000-000000000001'::UUID AS id,
       '{"a": 1}'::JSON AS doc,
       bitstring('0101', 4) AS bits,
       TIMESTAMPTZ '2024-01-02 03:04:05+00' AS at,
       [1, 2] AS list,
       {'x': 1.5::DECIMAL(4,1)} AS rec,
       'abc'::BLOB AS raw
"""


def _parquet(sql: str = MATRIX_SQL) -> bytes:
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "r.parquet")
        conn = duckdb.connect()
        try:
            conn.sql(sql).write_parquet(path)
        finally:
            conn.close()
        with open(path, "rb") as f:
            return f.read()


@pytest_asyncio.fixture
async def user(db_session):
    u = User(email="r@stream.local", password_hash=hash_password("pw"), name="R", role="user")
    db_session.add(u)
    await db_session.commit()
    await db_session.refresh(u)
    return u


@pytest_asyncio.fixture
async def authed_client(client: AsyncClient, user: User):
    await client.post("/auth/login", json={"email": "r@stream.local", "password": "pw"})
    return client


@pytest_asyncio.fixture
async def ws(db_session, user):
    workspace, catalog = await seed_workspace(db_session, user_id=user.id)
    return workspace, catalog


@pytest_asyncio.fixture
async def agent(db_session):
    a = Agent(name="result-agent", status="healthy", result_host="agent", result_port=8001)
    db_session.add(a)
    await db_session.commit()
    await db_session.refresh(a)
    return a


async def _done(db_session, ws, user, agent, **fields) -> Query:
    workspace, catalog = ws
    values = {
        "workspace_id": workspace.id,
        "agent_id": agent.id,
        "user_id": user.id,
        "sql": "SELECT * FROM analytics.t",
        "status": "done",
        "result_path": "/var/duckhaven-agent/results/x.parquet",
        "row_count": 1,
        "active_catalog": catalog.slug,
        **fields,
    }
    q = Query(**values)
    db_session.add(q)
    await db_session.commit()
    await db_session.refresh(q)
    return q


@pytest.fixture
def agent_file(monkeypatch):
    """Serve ``agent_file.content`` (or ``agent_file.status``) as the agent's result."""

    class Served:
        content = _parquet()
        status = 200
        opened = 0
        closed = 0

    served = Served()

    async def fake_open(source):
        served.opened += 1

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(served.status, content=served.content)

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        original_aclose = client.aclose

        async def aclose():
            served.closed += 1
            await original_aclose()

        client.aclose = aclose  # type: ignore[method-assign]
        response = await client.send(client.build_request("GET", source.url), stream=True)
        try:
            query_service._raise_for_upstream(response.status_code)
        except Exception:
            await response.aclose()
            await client.aclose()
            raise
        return client, response

    monkeypatch.setattr(query_service, "open_result_stream", fake_open)
    return served


async def test_parquet_is_the_agents_file_byte_for_byte(
    authed_client, db_session, ws, user, agent, agent_file
):
    q = await _done(db_session, ws, user, agent)
    resp = await authed_client.get(f"/queries/{q.id}/result", params={"format": "parquet"})
    assert resp.status_code == 200
    assert resp.headers["content-type"] == result_stream.PARQUET_MEDIA_TYPE
    assert resp.content == agent_file.content
    assert agent_file.closed == 1  # the upstream client is released


async def test_arrow_keeps_every_value_exact(
    authed_client, db_session, ws, user, agent, agent_file
):
    q = await _done(db_session, ws, user, agent)
    resp = await authed_client.get(f"/queries/{q.id}/result")  # arrow by default
    assert resp.status_code == 200
    assert resp.headers["content-type"] == result_stream.ARROW_MEDIA_TYPE
    table = pa.ipc.open_stream(resp.content).read_all()
    [row] = table.to_pylist()
    assert row["dec"] == Decimal("123456789012345678.1234567891")
    assert row["wide"] == Decimal("12345678901234567890123")
    assert row["big"] == 9007199254740993
    assert str(row["id"]) == "00000000-0000-0000-0000-000000000001"
    assert row["doc"] == '{"a": 1}'
    assert row["bits"] == "0101"
    assert row["at"].isoformat() == "2024-01-02T03:04:05+00:00"
    assert row["list"] == [1, 2]
    assert row["rec"] == {"x": Decimal("1.5")}
    assert row["raw"] == b"abc"


async def test_a_cached_result_streams_from_its_inline_copy(
    authed_client, db_session, ws, user, agent, monkeypatch
):
    content = _parquet("SELECT 42 AS answer")
    q = await _done(db_session, ws, user, agent)

    async def inline_source(db, query):
        return query_service.ResultSource(query=query, inline=content)

    monkeypatch.setattr(query_service, "resolve_result_source", inline_source)
    parquet = await authed_client.get(f"/queries/{q.id}/result", params={"format": "parquet"})
    assert parquet.content == content
    assert parquet.headers["content-length"] == str(len(content))
    arrow = await authed_client.get(f"/queries/{q.id}/result")
    assert pa.ipc.open_stream(arrow.content).read_all().to_pylist() == [{"answer": 42}]


@pytest.mark.parametrize(("upstream", "expected"), [(404, 410), (500, 502)])
@pytest.mark.parametrize("fmt", ["arrow", "parquet"])
async def test_an_unreadable_result_is_an_error_status_not_a_cut_stream(
    authed_client, db_session, ws, user, agent, agent_file, upstream, expected, fmt
):
    agent_file.status = upstream
    q = await _done(db_session, ws, user, agent)
    resp = await authed_client.get(f"/queries/{q.id}/result", params={"format": fmt})
    assert resp.status_code == expected
    assert agent_file.closed == 1


async def test_ddl_has_no_result(authed_client, db_session, ws, user, agent):
    q = await _done(db_session, ws, user, agent, result_path=None)
    assert (await authed_client.get(f"/queries/{q.id}/result")).status_code == 204


async def test_an_unfinished_query_has_no_result_yet(authed_client, db_session, ws, user, agent):
    q = await _done(db_session, ws, user, agent, status="running")
    assert (await authed_client.get(f"/queries/{q.id}/result")).status_code == 409


async def test_reading_needs_the_grants_running_it_would(
    authed_client, db_session, ws, user, agent, agent_file
):
    workspace, _ = ws
    await db_session.execute(
        update(WorkspaceCatalog)
        .where(WorkspaceCatalog.workspace_id == workspace.id)
        .values(access_mode="scoped")
    )
    await db_session.commit()
    q = await _done(db_session, ws, user, agent)
    resp = await authed_client.get(f"/queries/{q.id}/result")
    assert resp.status_code == 403
    assert agent_file.opened == 0


async def test_a_client_that_disconnects_leaves_no_spooled_file():
    """Starlette closes the body iterator on disconnect; the spooled Parquet must
    go with it, even though the thread-pool wrapper never closes its generator."""
    before = set(glob.glob(os.path.join(tempfile.gettempdir(), "dh-result-*.parquet")))
    source = query_service.ResultSource(query=Query(), inline=_parquet())
    body = await result_stream.open_arrow(source)
    first = await anext(body)
    assert first  # the schema and the first batch
    await body.aclose()
    after = set(glob.glob(os.path.join(tempfile.gettempdir(), "dh-result-*.parquet")))
    assert after == before


async def test_an_empty_result_is_still_a_readable_stream():
    """No batch carries the schema, so closing the writer must emit it."""
    source = query_service.ResultSource(
        query=Query(), inline=_parquet("SELECT 1::BIGINT AS n WHERE false")
    )
    body = await result_stream.open_arrow(source)
    content = b"".join([chunk async for chunk in body])
    table = pa.ipc.open_stream(content).read_all()
    assert table.num_rows == 0
    assert table.schema.names == ["n"]
