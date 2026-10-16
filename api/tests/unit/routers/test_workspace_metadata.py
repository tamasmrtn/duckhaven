"""GET /workspaces/{workspace}/metadata: what a caller may see, for drivers."""

from __future__ import annotations

import re

import pytest
from httpx import AsyncClient
from sqlalchemy import select, update

from api.config import settings
from api.models.catalog import Catalog, WorkspaceCatalog
from api.models.catalog_grant import CatalogGrant
from api.models.query import Query
from api.models.storage_backend import StorageBackend
from api.models.user import User
from api.models.workspace import Workspace, WorkspaceMember
from api.routers import metadata as metadata_router
from api.schemas.query import RowsPageOut
from api.services import query as query_service
from api.services.auth import hash_password

_COLS = [{"name": "id", "type": "INTEGER", "nullable": True}]


@pytest.fixture
async def owner(db_session) -> User:
    u = User(email="owner@meta.local", password_hash=hash_password("pw"), name="Owner", role="user")
    db_session.add(u)
    await db_session.commit()
    await db_session.refresh(u)
    return u


@pytest.fixture
async def auth_client(client: AsyncClient, owner: User) -> AsyncClient:
    await client.post("/auth/login", json={"email": "owner@meta.local", "password": "pw"})
    return client


@pytest.fixture
async def alpha(auth_client, db_session, owner, fake_polaris) -> str:
    """Workspace `alpha`, catalog `alpha`, with marketing.leads and finance.ledger."""
    sb = StorageBackend(kind="object_store", name="primary", root_uri="", created_by=owner.id)
    db_session.add(sb)
    await db_session.commit()
    await db_session.refresh(sb)
    assert (await auth_client.post("/workspaces", json={"slug": "alpha", "name": "A"})).is_success
    created = await auth_client.post(
        "/workspaces/alpha/catalogs", json={"name": "alpha", "storage_backend_id": str(sb.id)}
    )
    assert created.status_code == 201, created.text
    for schema, table in [("marketing", "leads"), ("finance", "ledger")]:
        await auth_client.post("/workspaces/alpha/catalogs/alpha/schemas", json={"name": schema})
        await auth_client.post(
            f"/workspaces/alpha/catalogs/alpha/schemas/{schema}/tables",
            json={"name": table, "columns": _COLS},
        )
    return "alpha"


@pytest.fixture
def agent(monkeypatch):
    """Stands in for the agent that describes: records each query, answers each
    table with its own name as the one column, or fails the tables in ``broken``."""

    class Agent:
        queries: list[str] = []
        broken: set[str] = set()

    state = Agent()
    state.queries = []
    state.broken = set()
    results: dict[int, list[dict]] = {}

    async def run(db, *, workspace, user_id, sql, origin, active_catalog, wait_s):
        assert origin == "describe"
        state.queries.append(sql)
        described = re.findall(
            r'SELECT (\d+) AS i.*?\(DESCRIBE "[^"]+"\."([^"]+)"\."([^"]+)"\)', sql
        )
        if any(t in state.broken for _, _, t in described):
            return Query(status="failed", error="Catalog Error")
        q = Query(status="done", result_path="/r")
        rows = [
            {"i": int(i), "column_name": f"{t}_id", "column_type": "BIGINT", "nullable": "YES"}
            for i, _, t in described
        ]
        results[id(q)] = rows
        return q

    async def page(db, query, *, limit, offset=0):
        rows = results[id(query)]
        return RowsPageOut(
            rows=rows[offset : offset + limit], columns=[], cursor=None, total=len(rows)
        )

    monkeypatch.setattr(query_service, "run_internal_query", run)
    monkeypatch.setattr(query_service, "fetch_result_page", page)
    return state


def _tables(body) -> dict[str, list | None]:
    return {
        f"{s['name']}.{t['name']}": t["columns"]
        for c in body["catalogs"]
        for s in c["schemas"]
        for t in s["tables"]
    }


async def test_names_need_no_agent(auth_client, alpha, agent):
    resp = await auth_client.get(f"/workspaces/{alpha}/metadata")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    [cat] = body["catalogs"]
    assert (cat["name"], cat["access_mode"]) == ("alpha", "open")
    assert _tables(body) == {"marketing.leads": None, "finance.ledger": None}
    assert agent.queries == []


async def test_columns_come_from_describe_in_duckdb_types(auth_client, alpha, agent):
    resp = await auth_client.get(f"/workspaces/{alpha}/metadata", params={"columns": "true"})
    assert _tables(resp.json()) == {
        "marketing.leads": [{"name": "leads_id", "type": "BIGINT", "nullable": True}],
        "finance.ledger": [{"name": "ledger_id", "type": "BIGINT", "nullable": True}],
    }
    # One query for the batch, tables numbered rather than named in literals.
    [sql] = agent.queries
    assert "'leads'" not in sql and '"alpha"."marketing"."leads"' in sql


async def test_filters_narrow_the_report(auth_client, alpha, agent):
    resp = await auth_client.get(
        f"/workspaces/{alpha}/metadata", params={"schema": "finance", "table": "ledger"}
    )
    assert _tables(resp.json()) == {"finance.ledger": None}
    missing = await auth_client.get(f"/workspaces/{alpha}/metadata", params={"catalog": "nope"})
    assert missing.status_code == 404


async def test_batches_split_and_a_bad_table_does_not_sink_the_rest(
    auth_client, alpha, agent, monkeypatch
):
    agent.broken = {"ledger"}
    resp = await auth_client.get(f"/workspaces/{alpha}/metadata", params={"columns": "true"})
    assert _tables(resp.json()) == {
        "marketing.leads": [{"name": "leads_id", "type": "BIGINT", "nullable": True}],
        "finance.ledger": None,
    }
    # The failed batch, then each table alone.
    assert len(agent.queries) == 3

    agent.broken, agent.queries = set(), []
    monkeypatch.setattr(metadata_router, "_BATCH_TABLES", 1)
    await auth_client.get(f"/workspaces/{alpha}/metadata", params={"columns": "true"})
    assert len(agent.queries) == 2


async def test_too_many_tables_is_a_422_that_says_how_to_narrow(
    auth_client, alpha, agent, monkeypatch
):
    monkeypatch.setattr(settings, "metadata_columns_max_tables", 1)
    resp = await auth_client.get(f"/workspaces/{alpha}/metadata", params={"columns": "true"})
    assert resp.status_code == 422
    assert resp.json()["error"] == "too_many_tables"
    assert agent.queries == []
    # Names alone are never capped.
    assert (await auth_client.get(f"/workspaces/{alpha}/metadata")).status_code == 200


async def test_a_scoped_catalog_shows_only_granted_objects(auth_client, alpha, agent, db_session):
    ws = (await db_session.execute(select(Workspace).where(Workspace.slug == alpha))).scalar_one()
    cat = (await db_session.execute(select(Catalog).where(Catalog.slug == "alpha"))).scalar_one()
    member = User(email="m@meta.local", password_hash=hash_password("pw"), name="M", role="user")
    db_session.add(member)
    await db_session.commit()
    await db_session.refresh(member)
    db_session.add(WorkspaceMember(workspace_id=ws.id, user_id=member.id, role="reader"))
    await db_session.execute(
        update(WorkspaceCatalog)
        .where(WorkspaceCatalog.workspace_id == ws.id)
        .values(access_mode="scoped")
    )
    db_session.add(
        CatalogGrant(
            user_id=member.id,
            catalog_id=cat.id,
            schema_name="marketing",
            table_name="leads",
            tier="metadata",
        )
    )
    await db_session.commit()
    await auth_client.post("/auth/login", json={"email": "m@meta.local", "password": "pw"})

    resp = await auth_client.get(f"/workspaces/{alpha}/metadata", params={"columns": "true"})
    body = resp.json()
    assert body["catalogs"][0]["access_mode"] == "scoped"
    assert _tables(body) == {
        "marketing.leads": [{"name": "leads_id", "type": "BIGINT", "nullable": True}]
    }
    assert all("ledger" not in sql for sql in agent.queries)


async def test_an_unreachable_schema_listing_leaves_the_rest(
    auth_client, alpha, agent, fake_polaris
):
    from api.services.polaris import PolarisError

    fake_polaris.raise_on_list_tables = PolarisError("boom")
    resp = await auth_client.get(f"/workspaces/{alpha}/metadata")
    assert resp.status_code == 200
    assert {s["name"] for s in resp.json()["catalogs"][0]["schemas"]} >= {"marketing", "finance"}
