"""Scoped-mode grant enforcement at SQL dispatch (issue #129)."""

from __future__ import annotations

import pytest_asyncio
from conftest import seed_workspace
from httpx import AsyncClient
from sqlalchemy import update

from api.models.agent import Agent
from api.models.catalog import WorkspaceCatalog
from api.models.catalog_grant import CatalogGrant
from api.models.user import User
from api.services.agent_registry import registry
from api.services.auth import hash_password


class MockWebSocket:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send_text(self, text: str) -> None:
        self.sent.append(text)


@pytest_asyncio.fixture
async def user(db_session):
    u = User(email="q@grants.local", password_hash=hash_password("pw"), name="Q", role="user")
    db_session.add(u)
    await db_session.commit()
    await db_session.refresh(u)
    return u


@pytest_asyncio.fixture
async def authed_client(client: AsyncClient, user: User):
    await client.post("/auth/login", json={"email": "q@grants.local", "password": "pw"})
    return client


@pytest_asyncio.fixture
async def connected_agent(db_session):
    a = Agent(name="test-agent", status="healthy", capabilities={"extensions": ["httpfs"]})
    db_session.add(a)
    await db_session.commit()
    await db_session.refresh(a)
    mock_ws = MockWebSocket()
    registry.register(a.id, mock_ws)  # type: ignore[arg-type]
    yield a
    registry.unregister(a.id)


@pytest_asyncio.fixture
async def scoped_ws(db_session, user: User):
    """A workspace (user is owner) whose one catalog is in scoped mode."""
    ws, cat = await seed_workspace(db_session, user_id=user.id)
    await db_session.execute(
        update(WorkspaceCatalog)
        .where(WorkspaceCatalog.workspace_id == ws.id, WorkspaceCatalog.catalog_id == cat.id)
        .values(access_mode="scoped")
    )
    await db_session.commit()
    return ws, cat


def _grant(db_session, user, cat, tier, schema="analytics", table=None):
    db_session.add(
        CatalogGrant(
            user_id=user.id, catalog_id=cat.id, schema_name=schema, table_name=table, tier=tier
        )
    )


async def _run(authed_client, ws, agent, sql):
    return await authed_client.post(
        f"/workspaces/{ws.slug}/queries", json={"sql": sql, "agent_id": str(agent.id)}
    )


async def test_scoped_dispatch_denied_without_grant(
    authed_client, scoped_ws, connected_agent, db_session
):
    ws, _cat = scoped_ws
    resp = await _run(authed_client, ws, connected_agent, "SELECT * FROM analytics.leads")
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "grant_denied"


async def test_scoped_dispatch_allowed_with_reader_grant(
    authed_client, scoped_ws, connected_agent, db_session, user
):
    ws, cat = scoped_ws
    _grant(db_session, user, cat, "reader", table="leads")
    await db_session.commit()
    resp = await _run(authed_client, ws, connected_agent, "SELECT * FROM analytics.leads")
    assert resp.status_code == 202


async def test_join_denied_if_any_table_lacks_reader(
    authed_client, scoped_ws, connected_agent, db_session, user
):
    ws, cat = scoped_ws
    _grant(db_session, user, cat, "reader", table="a")  # but not `b`
    await db_session.commit()
    sql = "SELECT * FROM analytics.a JOIN analytics.b ON a.id = b.id"
    resp = await _run(authed_client, ws, connected_agent, sql)
    assert resp.status_code == 403


async def test_write_requires_writer_on_target(
    authed_client, scoped_ws, connected_agent, db_session, user
):
    ws, cat = scoped_ws
    _grant(db_session, user, cat, "reader", table="t")  # reader is not enough to write
    await db_session.commit()
    resp = await _run(authed_client, ws, connected_agent, "INSERT INTO analytics.t VALUES (1)")
    assert resp.status_code == 403

    await db_session.execute(
        update(CatalogGrant)
        .where(CatalogGrant.catalog_id == cat.id, CatalogGrant.table_name == "t")
        .values(tier="writer")
    )
    await db_session.commit()
    resp = await _run(authed_client, ws, connected_agent, "INSERT INTO analytics.t VALUES (1)")
    assert resp.status_code == 202


async def test_info_schema_is_exempt(authed_client, scoped_ws, connected_agent, db_session):
    ws, _cat = scoped_ws
    # No grant, scoped catalog — but the metadata surface is always readable.
    resp = await _run(authed_client, ws, connected_agent, "SELECT * FROM information_schema.tables")
    assert resp.status_code == 202


async def test_open_mode_dispatch_needs_no_grant(authed_client, db_session, user, connected_agent):
    # A catalog left in default `open` mode dispatches without any grant.
    ws, _cat = await seed_workspace(db_session, user_id=user.id, slug="open-ws")
    resp = await _run(authed_client, ws, connected_agent, "SELECT * FROM analytics.leads")
    assert resp.status_code == 202
