"""POST /workspaces/{workspace}/queries/describe: a query's columns without running it."""

from __future__ import annotations

import pytest
import pytest_asyncio
from conftest import seed_workspace
from httpx import AsyncClient
from sqlalchemy import select, update

from api.config import settings
from api.models.agent import Agent
from api.models.catalog import WorkspaceCatalog
from api.models.catalog_grant import CatalogGrant
from api.models.query import Query
from api.models.user import User
from api.schemas.query import RowsPageOut
from api.services import query as query_service
from api.services import query_history
from api.services.agent_registry import registry
from api.services.auth import hash_password
from api.services.compute import service as compute_service


class MockWebSocket:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send_text(self, text: str) -> None:
        self.sent.append(text)


@pytest_asyncio.fixture
async def user(db_session):
    u = User(email="d@describe.local", password_hash=hash_password("pw"), name="D", role="user")
    db_session.add(u)
    await db_session.commit()
    await db_session.refresh(u)
    return u


@pytest_asyncio.fixture
async def authed_client(client: AsyncClient, user: User):
    await client.post("/auth/login", json={"email": "d@describe.local", "password": "pw"})
    return client


@pytest_asyncio.fixture
async def workspace(db_session, user):
    ws, _catalog = await seed_workspace(db_session, user_id=user.id)
    return ws


@pytest.fixture
def bound(monkeypatch):
    """Stand in for the agent: the DESCRIBE it was sent, and what it answers."""

    class Agent:
        sql: list[str] = []
        status = "done"
        error = None
        rows = [
            {"column_name": "n", "column_type": "BIGINT"},
            {"column_name": "amt", "column_type": "DECIMAL(18,2)"},
        ]

    agent = Agent()

    async def fake_run(db, *, workspace, user_id, sql, origin, active_catalog, agent, wait_s):
        Agent.sql.append(sql)
        assert origin == "describe"
        return Query(status=agent_state.status, error=agent_state.error, result_path="/r")

    async def fake_page(db, query, *, limit, offset=0):
        return RowsPageOut(rows=agent_state.rows, columns=[], cursor=None, total=2)

    agent_state = agent
    Agent.sql = []
    monkeypatch.setattr(query_service, "run_internal_query", fake_run)
    monkeypatch.setattr(query_service, "fetch_result_page", fake_page)
    return agent


async def _describe(client, workspace, **body):
    return await client.post(f"/workspaces/{workspace.slug}/queries/describe", json=body)


async def test_a_select_is_described_not_run(authed_client, workspace, bound):
    resp = await _describe(authed_client, workspace, sql="SELECT count(*) AS n, 1.5 AS amt")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["columns"] == [
        {"name": "n", "type": "BIGINT"},
        {"name": "amt", "type": "DECIMAL(18,2)"},
    ]
    assert body["statement_type"] == "select"
    assert bound.sql == ["DESCRIBE SELECT count(*) AS n, 1.5 AS amt"]


async def test_placeholders_without_values_are_described_as_null(authed_client, workspace, bound):
    resp = await _describe(authed_client, workspace, sql="SELECT * FROM t WHERE a = $2 OR b = $1")
    assert resp.json()["parameter_names"] == ["1", "2"]
    assert bound.sql == ["DESCRIBE SELECT * FROM t WHERE a = (NULL) OR b = (NULL)"]


async def test_given_values_are_used(authed_client, workspace, bound):
    resp = await _describe(
        authed_client, workspace, sql="SELECT $day AS d", parameters={"day": "2024-01-02"}
    )
    assert resp.json()["parameter_names"] == ["day"]
    assert bound.sql == ["DESCRIBE SELECT ('2024-01-02') AS d"]


async def test_a_statement_without_a_grid_needs_no_agent(authed_client, workspace, bound):
    resp = await _describe(authed_client, workspace, sql="INSERT INTO t VALUES (1)")
    assert resp.status_code == 200
    assert resp.json() == {"statement_type": "insert", "columns": None, "parameter_names": []}
    assert bound.sql == []


@pytest.mark.parametrize(
    "sql", ["DESCRIBE t", "SHOW TABLES", "SUMMARIZE t", "SELECT 1; SELECT 2", "SELEC 1"]
)
async def test_what_cannot_be_described_is_a_422(authed_client, workspace, bound, sql):
    resp = await _describe(authed_client, workspace, sql=sql)
    assert resp.status_code == 422
    assert resp.json()["error"] == "not_describable"
    assert bound.sql == []


async def test_sql_the_gate_refuses_is_refused_before_any_agent(authed_client, workspace, bound):
    resp = await _describe(authed_client, workspace, sql="SELECT * FROM query('SELECT 1')")
    assert resp.status_code == 422
    assert bound.sql == []


async def test_a_statement_that_does_not_bind_says_why(authed_client, workspace, bound):
    bound.status, bound.error = "failed", "Catalog Error: Table with name t does not exist!"
    resp = await _describe(authed_client, workspace, sql="SELECT * FROM t")
    assert resp.status_code == 422
    assert "does not exist" in resp.json()["message"]


async def test_compute_starting_is_a_503_with_retry_after(authed_client, workspace, monkeypatch):
    async def starting(*args, **kwargs):
        raise query_service.ComputeStarting("Compute is starting; retry shortly")

    monkeypatch.setattr(query_service, "run_internal_query", starting)
    resp = await _describe(authed_client, workspace, sql="SELECT 1")
    assert resp.status_code == 503
    assert resp.json()["error"] == "compute_starting"
    assert resp.headers["retry-after"] == "5"


async def test_a_described_pivot_needs_reader(authed_client, db_session, workspace, user):
    """The real dispatch path: the grant check sees the DESCRIBE and the PIVOT."""
    await db_session.execute(
        update(WorkspaceCatalog)
        .where(WorkspaceCatalog.workspace_id == workspace.id)
        .values(access_mode="scoped")
    )
    catalog_id = (
        await db_session.execute(
            select(WorkspaceCatalog.catalog_id).where(WorkspaceCatalog.workspace_id == workspace.id)
        )
    ).scalar_one()
    db_session.add(
        CatalogGrant(
            user_id=user.id, catalog_id=catalog_id, schema_name="analytics", tier="metadata"
        )
    )
    agent = Agent(
        name="describe-agent",
        status="healthy",
        capabilities={"duckdb_version": "1.5.5", "extensions": ["httpfs"]},
    )
    db_session.add(agent)
    await db_session.commit()
    registry.register(agent.id, MockWebSocket())  # type: ignore[arg-type]
    try:
        # A static pivot is one statement and binds: the grant check needs reader.
        static = await _describe(
            authed_client,
            workspace,
            sql="SELECT * FROM (PIVOT analytics.leads ON status IN ('a', 'b') USING count(*))",
            agent_id=str(agent.id),
        )
        assert static.status_code == 403, static.text
        # A dynamic pivot reads its values in a statement of its own (a CREATE
        # TYPE), so it is not one describable statement at all.
        dynamic = await _describe(
            authed_client,
            workspace,
            sql="SELECT * FROM (PIVOT analytics.leads ON status USING count(*))",
            agent_id=str(agent.id),
        )
        assert dynamic.status_code == 422
    finally:
        registry.unregister(agent.id)


# --- starting compute for a describe -----------------------------------------


async def test_with_no_agent_a_describe_starts_compute_and_gives_up_cleanly(
    db_session, workspace, user, monkeypatch
):
    started = []

    async def ensure_agent(db, pool_key):
        started.append(pool_key)

    monkeypatch.setattr(settings, "elastic_compute_enabled", True)
    monkeypatch.setattr(compute_service, "ensure_agent", ensure_agent)
    with pytest.raises(query_service.ComputeStarting):
        await query_service.run_internal_query(
            db_session,
            workspace=workspace,
            user_id=user.id,
            sql="DESCRIBE SELECT 1",
            origin="describe",
            wait_s=0.3,
        )
    assert len(started) == 1
    [parked] = (await db_session.execute(select(Query))).scalars().all()
    assert (parked.origin, parked.status, parked.agent_id) == ("describe", "cancelled", None)


async def test_without_elastic_compute_no_agent_is_unavailable(db_session, workspace, user):
    with pytest.raises(query_service.AgentUnavailable):
        await query_service.run_internal_query(
            db_session,
            workspace=workspace,
            user_id=user.id,
            sql="DESCRIBE SELECT 1",
            origin="describe",
            wait_s=0.1,
        )


def test_describes_stay_out_of_history():
    assert "describe" in query_history.HIDDEN_ORIGINS
