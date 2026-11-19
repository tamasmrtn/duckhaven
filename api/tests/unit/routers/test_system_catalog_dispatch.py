"""What dispatch sends for a statement that reads the `duckhaven` system catalog,
and what it refuses when the catalog cannot be attached."""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from conftest import seed_workspace
from httpx import AsyncClient
from sqlalchemy import func, select

from api.config import settings
from api.models.agent import Agent
from api.models.query import Query
from api.models.rbac import Role, RolePermission
from api.models.sql_session import SqlSession
from api.models.user import Credential, User
from api.models.workspace import WorkspaceMember
from api.services import query as query_service
from api.services import runtimes as runtime_service
from api.services.agent_registry import registry
from api.services.auth import hash_password
from api.services.catalog_requests import answer_catalog_request
from api.services.permissions import Permission
from api.services.sql_sessions import service as session_service
from api.services.system_catalog import store
from api.services.workspace import resolve_workspace_catalogs
from duckhaven_shared.protocol import Frame, FrameType
from duckhaven_shared.schemas import CatalogAttach

READS = "SELECT action FROM duckhaven.access.audit;"
FEATURES = [
    "statement_ack",
    "on_demand_attach",
    "batch_dispatch",
    "system_catalog",
    "system_catalog_row_filter",
]
INFO = ",".join(
    f"info_schema.{t}"
    for t in sorted(
        [
            "catalogs",
            "columns",
            "routines",
            "schemata",
            "semantic_datasets",
            "semantic_dimensions",
            "semantic_metrics",
            "semantic_models",
            "semantic_relationships",
            "tables",
        ]
    )
)
AUDITED = (
    "access.audit,access.credentials,access.grants,access.table_access,access.users,"
    f"access.workspaces,{INFO},lineage.column_lineage,lineage.table_lineage"
)
ALL_TABLES = ",".join(
    sorted(
        [
            *AUDITED.split(","),
            "ai.assistant_usage",
            "ai.tool_calls",
            "compute.agent_events",
            "compute.agent_utilization",
            "compute.agents",
            "jobs.schedules",
            "query.history",
            "query.sessions",
            "storage.backends",
            "storage.maintenance_operations",
            "storage.table_health",
        ]
    )
)


class MockWebSocket:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send_text(self, text: str) -> None:
        self.sent.append(text)

    def frames(self, kind: FrameType) -> list[Frame]:
        return [f for f in map(Frame.model_validate_json, self.sent) if f.type == kind]


async def _user(db, email: str, role: str) -> User:
    u = User(email=email, password_hash=hash_password("pw"), name=email, role=role)
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


@pytest_asyncio.fixture
async def admin(db_session) -> User:
    return await _user(db_session, "admin@sysdispatch.local", "admin")


@pytest_asyncio.fixture
async def member(db_session) -> User:
    return await _user(db_session, "member@sysdispatch.local", "user")


@pytest_asyncio.fixture
async def as_admin(client: AsyncClient, admin: User) -> AsyncClient:
    await client.post("/auth/login", json={"email": admin.email, "password": "pw"})
    return client


@pytest_asyncio.fixture
async def workspace(db_session, admin, member):
    ws, _cat = await seed_workspace(db_session, user_id=admin.id, catalog_slug="sales")
    db_session.add(WorkspaceMember(workspace_id=ws.id, user_id=member.id, role="writer"))
    await db_session.commit()
    return ws


@pytest.fixture
def available(db_session, monkeypatch):
    """A store this replica has created, with its reader's password."""
    monkeypatch.setattr(settings, "system_catalog_enabled", True)
    monkeypatch.setattr(store, "is_available", lambda: True)
    db_session.add(Credential(kind=store.READER_CREDENTIAL_KIND, token="reader-pw"))


async def _agent(db, *, features=FEATURES, extensions=("httpfs", "iceberg", "postgres_scanner")):
    agent = Agent(
        name=f"agent-{uuid.uuid4().hex[:6]}",
        status="healthy",
        capabilities={
            "duckdb_version": "1.5.5",
            "extensions": list(extensions),
            "protocol_features": features,
        },
    )
    db.add(agent)
    await db.commit()
    await db.refresh(agent)
    sock = MockWebSocket()
    registry.register(agent.id, sock)  # type: ignore[arg-type]
    return agent, sock


@pytest.fixture(autouse=True)
def _clear_registry():
    yield
    for cid in list(registry.connected_ids()):
        registry.unregister(uuid.UUID(cid))


async def _run(client, ws, agent, sql=READS):
    return await client.post(
        f"/workspaces/{ws.slug}/queries", json={"sql": sql, "agent_id": str(agent.id)}
    )


async def _queries(db) -> int:
    return await db.scalar(select(func.count()).select_from(Query))


# --- one-shot queries ----------------------------------------------------------


async def test_a_read_attaches_it_last_as_the_reader(
    as_admin, db_session, workspace, admin, available
):
    agent, sock = await _agent(db_session)

    resp = await _run(as_admin, workspace, agent)

    assert resp.status_code == 202, resp.text
    (frame,) = sock.frames(FrameType.DISPATCH_QUERY)
    *workspace_side, system = frame.payload["catalogs"]
    assert [c["slug"] for c in workspace_side] == ["sales"]
    assert system == {
        "slug": "duckhaven",
        "kind": "system",
        "backend": {},
        "default_schema": "access",
        "meta": {
            "host": settings.system_catalog_agent_host,
            "port": settings.system_catalog_agent_port,
            "database": settings.system_catalog_agent_database,
            "user": "dh_system_reader",
            "password": "reader-pw",
            # Who it runs for, for the store's row filters.
            "settings": {
                "duckhaven.workspace": str(workspace.id),
                "duckhaven.principals": str(admin.id),
                "duckhaven.readable": ALL_TABLES,
            },
        },
    }
    CatalogAttach.model_validate(system)
    # Never one an agent may ask for later.
    assert frame.payload["workspace_catalogs"] == ["sales"]
    assert frame.payload["preload"]["catalog_kinds"] == ["iceberg_polaris"]


async def test_a_statement_that_does_not_read_it_never_carries_it(
    as_admin, db_session, workspace, available
):
    agent, sock = await _agent(db_session)

    resp = await _run(as_admin, workspace, agent, "SELECT 'duckhaven' AS product;")

    assert resp.status_code == 202, resp.text
    (frame,) = sock.frames(FrameType.DISPATCH_QUERY)
    assert [c["slug"] for c in frame.payload["catalogs"]] == ["sales"]


@pytest.mark.parametrize("enabled", [True, False])
async def test_without_its_store_a_read_is_refused(
    as_admin, db_session, workspace, monkeypatch, enabled
):
    """Turned off, or this replica has not created the store (its database is missing)."""
    monkeypatch.setattr(settings, "system_catalog_enabled", enabled)
    monkeypatch.setattr(store, "is_available", lambda: enabled)
    agent, sock = await _agent(db_session)

    resp = await _run(as_admin, workspace, agent)

    assert resp.status_code == 422, resp.text
    assert resp.json()["error"] == "system_catalog_unavailable"
    assert sock.frames(FrameType.DISPATCH_QUERY) == []
    assert await _queries(db_session) == 0


@pytest.mark.parametrize(
    ("features", "extensions", "says"),
    [
        (FEATURES[:-2], ("httpfs", "iceberg", "postgres_scanner"), "too old"),
        # One that would attach without saying who it runs for, and read nothing.
        (FEATURES[:-1], ("httpfs", "iceberg", "postgres_scanner"), "too old"),
        (FEATURES, ("httpfs", "iceberg"), "postgres_scanner"),
    ],
)
async def test_an_agent_that_cannot_attach_it_is_refused(
    as_admin, db_session, workspace, available, features, extensions, says
):
    agent, sock = await _agent(db_session, features=features, extensions=extensions)

    resp = await _run(as_admin, workspace, agent)

    assert resp.status_code == 422, resp.text
    assert resp.json()["error"] == "agent_incompatible"
    assert says in resp.text
    assert sock.frames(FrameType.DISPATCH_QUERY) == []


async def test_a_batch_attaches_it_once_for_the_query_that_reads_it(
    db_session, workspace, admin, available
):
    agent, sock = await _agent(db_session)
    queries = [
        Query(workspace_id=workspace.id, agent_id=agent.id, user_id=admin.id, sql=sql)
        for sql in ("SELECT 1;", READS)
    ]
    db_session.add_all(queries)
    await db_session.commit()

    assert await query_service.dispatch_batch(db_session, queries) == [None, None]

    (frame,) = sock.frames(FrameType.DISPATCH_BATCH)
    assert [c["slug"] for c in frame.payload["catalogs"]] == ["sales", "duckhaven"]


async def test_a_query_run_for_someone_reads_only_what_both_may(
    db_session, workspace, admin, available
):
    """The assistant's runs: the store narrows them to what the person may read too."""
    role = Role(name="auditor")
    db_session.add(role)
    await db_session.flush()
    db_session.add(RolePermission(role_id=role.id, permission=Permission.AUDIT_READ.value))
    auditor = await _user(db_session, "auditor@sysdispatch.local", "auditor")
    db_session.add(WorkspaceMember(workspace_id=workspace.id, user_id=auditor.id, role="reader"))
    agent, sock = await _agent(db_session)
    query = Query(
        workspace_id=workspace.id,
        agent_id=agent.id,
        user_id=admin.id,
        on_behalf_of_id=auditor.id,
        sql=READS,
    )
    db_session.add(query)
    await db_session.commit()

    assert await query_service.dispatch_batch(db_session, [query]) == [None]

    (frame,) = sock.frames(FrameType.DISPATCH_BATCH)
    settings_sent = frame.payload["catalogs"][-1]["meta"]["settings"]
    assert settings_sent["duckhaven.principals"] == f"{admin.id},{auditor.id}"
    assert settings_sent["duckhaven.readable"] == AUDITED


# --- SQL sessions --------------------------------------------------------------


async def _session(db, ws, agent, user) -> SqlSession:
    s = SqlSession(
        workspace_id=ws.id,
        agent_id=agent.id,
        user_id=user.id,
        status="opening",
        active_catalog="sales",
        staging_uri="/tmp/test/_staging/x/",
    )
    db.add(s)
    await db.commit()
    await db.refresh(s)
    return s


@pytest.fixture
def captured(monkeypatch) -> list[Frame]:
    frames: list[Frame] = []

    async def fake_send(db, agent_id, raw):
        frames.append(Frame.model_validate_json(raw))
        return True

    monkeypatch.setattr(session_service, "send_to_agent", fake_send)
    monkeypatch.setattr(runtime_service, "assert_dispatchable", lambda *a, **k: None)
    return frames


@pytest.mark.parametrize("who", ["admin", "member"])
async def test_a_session_opens_ready_to_attach_it(
    request, db_session, workspace, available, captured, who
):
    """Its extension must load before the sandbox locks; the catalog itself waits
    for a statement that reads it. Every member may read info_schema."""
    agent, _sock = await _agent(db_session)
    session = await _session(db_session, workspace, agent, request.getfixturevalue(who))

    await session_service.dispatch_open_session(
        db_session, session, await resolve_workspace_catalogs(db_session, workspace.id)
    )

    (frame,) = captured
    assert [c["slug"] for c in frame.payload["catalogs"]] == ["sales"]
    assert "system" in frame.payload["preload"]["catalog_kinds"]


async def test_a_session_on_a_deployment_without_it_does_not_prepare_for_it(
    db_session, workspace, admin, captured, monkeypatch
):
    monkeypatch.setattr(store, "is_available", lambda: False)
    agent, _sock = await _agent(db_session)
    session = await _session(db_session, workspace, agent, admin)

    await session_service.dispatch_open_session(
        db_session, session, await resolve_workspace_catalogs(db_session, workspace.id)
    )

    (frame,) = captured
    assert frame.payload["preload"]["catalog_kinds"] == ["iceberg_polaris"]


async def test_a_session_statement_carries_it_last(
    db_session, workspace, admin, available, captured
):
    agent, _sock = await _agent(db_session)
    session = await _session(db_session, workspace, agent, admin)
    query = Query(workspace_id=workspace.id, agent_id=agent.id, sql=READS)
    db_session.add(query)
    await db_session.commit()

    await session_service.dispatch_exec_statement(
        db_session,
        session,
        query,
        30.0,
        await resolve_workspace_catalogs(db_session, workspace.id),
        system={"slug": "duckhaven", "kind": "system"},
    )

    (frame,) = captured
    assert [c["slug"] for c in frame.payload["catalogs"]] == ["sales", "duckhaven"]


@pytest.fixture
def sessions_on(monkeypatch):
    monkeypatch.setattr(settings, "sql_sessions_enabled", True)
    monkeypatch.setattr(settings, "sql_statement_wait_timeout_s", 0.0)


@pytest.fixture
def sent(monkeypatch) -> list[dict | None]:
    """The system descriptor each session statement was dispatched with."""
    systems: list[dict | None] = []

    async def fake_exec(db, session, query, timeout_s, catalogs, *, system=None):
        systems.append(system)
        return True

    monkeypatch.setattr(session_service, "dispatch_exec_statement", fake_exec)
    return systems


async def _statement(client, db, ws, user, sql=READS):
    agent, _sock = await _agent(db)
    session = await _session(db, ws, agent, user)
    session.status = "open"
    await db.commit()
    return await client.post(f"/sql/sessions/{session.id}/statements", json={"sql": sql})


async def test_a_session_statement_that_reads_it_is_sent_with_it(
    as_admin, db_session, workspace, admin, available, sessions_on, sent
):
    resp = await _statement(as_admin, db_session, workspace, admin)

    assert resp.status_code == 202, resp.text
    (system,) = sent
    assert (system["slug"], system["meta"]["password"]) == ("duckhaven", "reader-pw")
    assert system["meta"]["settings"]["duckhaven.principals"] == str(admin.id)
    assert system["meta"]["settings"]["duckhaven.readable"] == ALL_TABLES


async def test_a_session_statement_it_cannot_serve_leaves_no_statement(
    as_admin, db_session, workspace, admin, sessions_on, sent, monkeypatch
):
    monkeypatch.setattr(store, "is_available", lambda: False)

    resp = await _statement(as_admin, db_session, workspace, admin)

    assert resp.status_code == 422, resp.text
    assert resp.json()["error"] == "system_catalog_unavailable"
    assert sent == []
    assert await _queries(db_session) == 0


async def test_a_session_statement_is_checked_for_its_owner(
    client, db_session, workspace, member, available, sessions_on, sent
):
    await client.post("/auth/login", json={"email": member.email, "password": "pw"})

    resp = await _statement(client, db_session, workspace, member)

    assert resp.status_code == 403, resp.text
    assert resp.json()["error"] == "grant_denied"
    assert sent == []


# --- CATALOG_REQUEST -----------------------------------------------------------


async def test_an_agent_cannot_ask_for_it(db_session, workspace, admin, available):
    """A view or macro cannot reach it: only a statement naming it brings it."""
    agent, _sock = await _agent(db_session)
    query = Query(
        workspace_id=workspace.id,
        agent_id=agent.id,
        user_id=admin.id,
        sql="SELECT * FROM v;",
        status="running",
    )
    db_session.add(query)
    await db_session.commit()

    answer = await answer_catalog_request(
        db_session,
        agent.id,
        {"request_id": "r1", "query_id": str(query.id), "catalog": "duckhaven"},
    )

    assert answer["catalog"] is None
