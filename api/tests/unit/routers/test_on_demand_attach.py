"""On-demand attach in the control plane: what dispatch sends, and how it answers
an agent asking for a catalog a statement turned out to need."""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from conftest import seed_workspace
from httpx import AsyncClient
from sqlalchemy import update

from api.models.agent import Agent
from api.models.catalog import Catalog, WorkspaceCatalog
from api.models.catalog_grant import CatalogGrant
from api.models.query import Query, SavedQuery, Schedule
from api.models.sql_session import SqlSession
from api.models.storage_backend import StorageBackend
from api.models.user import User
from api.services import runtimes as runtime_service
from api.services.agent_registry import registry
from api.services.auth import hash_password
from api.services.catalog_requests import answer_catalog_request
from api.services.sql_sessions import service as session_service
from duckhaven_shared.protocol import Frame, FrameType


class MockWebSocket:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send_text(self, text: str) -> None:
        self.sent.append(text)

    def frames(self) -> list[Frame]:
        return [Frame.model_validate_json(t) for t in self.sent]


@pytest_asyncio.fixture
async def user(db_session):
    u = User(email="od@attach.local", password_hash=hash_password("pw"), name="OD", role="user")
    db_session.add(u)
    await db_session.commit()
    await db_session.refresh(u)
    return u


@pytest_asyncio.fixture
async def authed_client(client: AsyncClient, user: User):
    await client.post("/auth/login", json={"email": "od@attach.local", "password": "pw"})
    return client


@pytest_asyncio.fixture
async def workspace(db_session, user: User):
    """Default catalog `test_ws` plus `sales` and `hr`, all Iceberg."""
    ws, default = await seed_workspace(db_session, user_id=user.id)
    extra = {}
    for slug in ("sales", "hr"):
        backend = StorageBackend(
            kind="object_store", name=f"{slug}-store", root_uri="/tmp/test", created_by=user.id
        )
        db_session.add(backend)
        await db_session.flush()
        cat = Catalog(
            slug=slug,
            name=slug,
            polaris_name=f"wh-{slug}",
            storage_backend_id=backend.id,
            created_by=user.id,
        )
        db_session.add(cat)
        await db_session.flush()
        db_session.add(
            WorkspaceCatalog(
                workspace_id=ws.id, catalog_id=cat.id, is_default=False, attached_by=user.id
            )
        )
        extra[slug] = cat
    await db_session.commit()
    return ws, default, extra


def _agent(features: list[str]) -> Agent:
    return Agent(
        name=f"agent-{uuid.uuid4().hex[:6]}",
        status="healthy",
        capabilities={
            "duckdb_version": "1.5.5",
            "extensions": ["httpfs", "iceberg"],
            "protocol_features": features,
        },
    )


@pytest_asyncio.fixture
async def on_demand_agent(db_session):
    a = _agent(["statement_ack", "on_demand_attach"])
    db_session.add(a)
    await db_session.commit()
    await db_session.refresh(a)
    sock = MockWebSocket()
    registry.register(a.id, sock)  # type: ignore[arg-type]
    yield a, sock
    registry.unregister(a.id)


@pytest_asyncio.fixture
async def older_agent(db_session):
    a = _agent(["statement_ack"])
    db_session.add(a)
    await db_session.commit()
    await db_session.refresh(a)
    sock = MockWebSocket()
    registry.register(a.id, sock)  # type: ignore[arg-type]
    yield a, sock
    registry.unregister(a.id)


async def _dispatched(client, ws, agent, sock, sql) -> dict:
    resp = await client.post(
        f"/workspaces/{ws.slug}/queries",
        json={"sql": sql, "agent_id": str(agent.id), "use_cache": False},
    )
    assert resp.status_code == 202, resp.text
    frame = next(f for f in sock.frames() if f.type == FrameType.DISPATCH_QUERY)
    return frame.payload


# --- dispatch ----------------------------------------------------------------


async def test_a_query_sends_only_the_catalogs_it_names(authed_client, workspace, on_demand_agent):
    ws, _default, _extra = workspace
    agent, sock = on_demand_agent

    payload = await _dispatched(authed_client, ws, agent, sock, "SELECT * FROM sales.analytics.t")

    assert sorted(c["slug"] for c in payload["catalogs"]) == ["sales", "test_ws"]
    assert sorted(payload["workspace_catalogs"]) == ["hr", "sales", "test_ws"]
    assert payload["preload"]["catalog_kinds"] == ["iceberg_polaris"]


async def test_a_listing_still_sends_every_catalog(authed_client, workspace, on_demand_agent):
    ws, _default, _extra = workspace
    agent, sock = on_demand_agent

    payload = await _dispatched(
        authed_client, ws, agent, sock, "SELECT * FROM information_schema.tables"
    )

    assert sorted(c["slug"] for c in payload["catalogs"]) == ["hr", "sales", "test_ws"]


async def test_an_older_agent_still_gets_every_catalog(authed_client, workspace, older_agent):
    ws, _default, _extra = workspace
    agent, sock = older_agent

    payload = await _dispatched(authed_client, ws, agent, sock, "SELECT 1")

    assert sorted(c["slug"] for c in payload["catalogs"]) == ["hr", "sales", "test_ws"]
    assert "workspace_catalogs" not in payload


async def _session(db, ws, agent, user) -> SqlSession:
    s = SqlSession(
        workspace_id=ws.id,
        agent_id=agent.id,
        user_id=user.id,
        status="opening",
        active_catalog="test_ws",
        staging_uri="/tmp/test/_staging/x/",
    )
    db.add(s)
    await db.commit()
    await db.refresh(s)
    return s


@pytest.fixture
def captured_frames(monkeypatch) -> list[Frame]:
    frames: list[Frame] = []

    async def fake_send(db, agent_id, raw):
        frames.append(Frame.model_validate_json(raw))
        return True

    monkeypatch.setattr(session_service, "send_to_agent", fake_send)
    monkeypatch.setattr(runtime_service, "assert_dispatchable", lambda *a, **k: None)
    return frames


async def test_a_session_opens_with_the_active_catalog_alone(
    db_session, workspace, on_demand_agent, user, captured_frames
):
    from api.services.workspace import resolve_workspace_catalogs

    ws, _default, _extra = workspace
    agent, _sock = on_demand_agent
    session = await _session(db_session, ws, agent, user)

    await session_service.dispatch_open_session(
        db_session, session, await resolve_workspace_catalogs(db_session, ws.id)
    )

    (frame,) = captured_frames
    assert [c["slug"] for c in frame.payload["catalogs"]] == ["test_ws"]
    assert sorted(frame.payload["workspace_catalogs"]) == ["hr", "sales", "test_ws"]


async def test_a_session_statement_carries_the_catalogs_it_names(
    db_session, workspace, on_demand_agent, user, captured_frames
):
    from api.services.workspace import resolve_workspace_catalogs

    ws, _default, _extra = workspace
    agent, _sock = on_demand_agent
    session = await _session(db_session, ws, agent, user)
    query = Query(workspace_id=ws.id, agent_id=agent.id, sql="SELECT * FROM hr.analytics.staff")
    db_session.add(query)
    await db_session.commit()

    await session_service.dispatch_exec_statement(
        db_session, session, query, 30.0, await resolve_workspace_catalogs(db_session, ws.id)
    )

    (frame,) = captured_frames
    assert sorted(c["slug"] for c in frame.payload["catalogs"]) == ["hr", "test_ws"]


# --- CATALOG_REQUEST ------------------------------------------------------------


async def _running(db, ws, agent, *, user_id=None, schedule_id=None, status="running") -> Query:
    q = Query(
        workspace_id=ws.id,
        agent_id=agent.id,
        user_id=user_id,
        schedule_id=schedule_id,
        sql="SELECT * FROM v",
        status=status,
    )
    db.add(q)
    await db.commit()
    await db.refresh(q)
    return q


async def _ask(db, agent, query, slug="sales") -> dict:
    return await answer_catalog_request(
        db, agent.id, {"request_id": "r1", "query_id": str(query.id), "catalog": slug}
    )


async def test_a_running_statement_gets_a_workspace_catalog(
    db_session, workspace, on_demand_agent, user
):
    ws, _default, _extra = workspace
    agent, _sock = on_demand_agent
    query = await _running(db_session, ws, agent, user_id=user.id)

    answer = await _ask(db_session, agent, query, "SALES")

    assert answer["request_id"] == "r1"
    assert answer["catalog"]["slug"] == "sales"
    assert answer["catalog"]["polaris_name"] == "wh-sales"


async def test_a_catalog_outside_the_workspace_is_refused(
    db_session, workspace, on_demand_agent, user
):
    ws, _default, _extra = workspace
    agent, _sock = on_demand_agent
    query = await _running(db_session, ws, agent, user_id=user.id)

    answer = await _ask(db_session, agent, query, "elsewhere")

    assert answer["catalog"] is None


@pytest.mark.parametrize("status", ["done", "failed", "cancelled"])
async def test_a_finished_statement_gets_nothing(
    db_session, workspace, on_demand_agent, user, status
):
    ws, _default, _extra = workspace
    agent, _sock = on_demand_agent
    query = await _running(db_session, ws, agent, user_id=user.id, status=status)

    assert (await _ask(db_session, agent, query))["catalog"] is None


async def test_an_agent_cannot_ask_on_another_agents_statement(
    db_session, workspace, on_demand_agent, older_agent, user
):
    ws, _default, _extra = workspace
    agent, _sock = on_demand_agent
    other, _ = older_agent
    query = await _running(db_session, ws, other, user_id=user.id)

    assert (await _ask(db_session, agent, query))["catalog"] is None


async def _scope(db, ws, catalog) -> None:
    await db.execute(
        update(WorkspaceCatalog)
        .where(WorkspaceCatalog.workspace_id == ws.id, WorkspaceCatalog.catalog_id == catalog.id)
        .values(access_mode="scoped")
    )
    await db.commit()


@pytest.mark.parametrize(
    ("grant_schema", "allowed"),
    [(None, True), ("analytics", False), ("nothing", False)],
)
async def test_a_scoped_catalog_needs_a_grant_on_all_of_it(
    db_session, workspace, on_demand_agent, user, grant_schema, allowed
):
    """The request does not say which object the view reads, so only a grant on
    the whole catalog covers it."""
    ws, _default, extra = workspace
    agent, _sock = on_demand_agent
    await _scope(db_session, ws, extra["sales"])
    if grant_schema != "nothing":
        db_session.add(
            CatalogGrant(
                user_id=user.id,
                catalog_id=extra["sales"].id,
                schema_name=grant_schema,
                tier="reader",
            )
        )
        await db_session.commit()
    query = await _running(db_session, ws, agent, user_id=user.id)

    answer = await _ask(db_session, agent, query)

    assert (answer["catalog"] is not None) is allowed
    if not allowed:
        assert "scoped" in answer["error"]


async def test_a_scheduled_run_is_checked_against_its_saved_querys_editor(
    db_session, workspace, on_demand_agent, user
):
    ws, _default, extra = workspace
    agent, _sock = on_demand_agent
    await _scope(db_session, ws, extra["sales"])
    db_session.add(CatalogGrant(user_id=user.id, catalog_id=extra["sales"].id, tier="reader"))
    saved = SavedQuery(
        workspace_id=ws.id, name="s", sql="SELECT 1", created_by=user.id, updated_by=user.id
    )
    db_session.add(saved)
    await db_session.flush()
    schedule = Schedule(
        workspace_id=ws.id, saved_query_id=saved.id, cron="0 * * * *", created_by=user.id
    )
    db_session.add(schedule)
    await db_session.commit()
    query = await _running(db_session, ws, agent, schedule_id=schedule.id)

    assert (await _ask(db_session, agent, query))["catalog"] is not None
