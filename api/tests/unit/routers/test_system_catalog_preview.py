"""`GET /workspaces/{ws}/system-catalog/schemas/{schema}/tables/{table}/sample`:
a system table's preview, for its page in the catalog."""

from __future__ import annotations

import os
import tempfile
import uuid

import duckdb
import httpx
import pytest
import pytest_asyncio
from conftest import seed_workspace
from httpx import AsyncClient

from api.config import settings
from api.models.agent import Agent
from api.models.query import Query
from api.models.user import Credential, User
from api.models.workspace import WorkspaceMember
from api.services import query as query_service
from api.services.agent_registry import registry
from api.services.auth import hash_password
from api.services.system_catalog import store
from duckhaven_shared.protocol import Frame, FrameType

FEATURES = ["statement_ack", "on_demand_attach", "batch_dispatch", "system_catalog"]
AUDIT_SQL = 'SELECT * FROM "duckhaven"."access"."audit" LIMIT 20'


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


async def _login(client: AsyncClient, user: User) -> AsyncClient:
    await client.post("/auth/login", json={"email": user.email, "password": "pw"})
    return client


@pytest_asyncio.fixture
async def admin(db_session) -> User:
    return await _user(db_session, "admin@syspreview.local", "admin")


@pytest_asyncio.fixture
async def workspace(db_session, admin):
    ws, _cat = await seed_workspace(db_session, user_id=admin.id, catalog_slug="sales")
    return ws


@pytest.fixture(autouse=True)
def available(db_session, monkeypatch):
    """A store this replica has created, with its reader's password."""
    monkeypatch.setattr(settings, "system_catalog_enabled", True)
    monkeypatch.setattr(store, "is_available", lambda: True)
    db_session.add(Credential(kind=store.READER_CREDENTIAL_KIND, token="reader-pw"))


@pytest.fixture(autouse=True)
def _clear_registry():
    yield
    for cid in list(registry.connected_ids()):
        registry.unregister(uuid.UUID(cid))


async def _agent(db, monkeypatch) -> tuple[Agent, MockWebSocket]:
    """A connected agent that can attach the system catalog, picked for the preview."""
    agent = Agent(
        name=f"agent-{uuid.uuid4().hex[:6]}",
        status="healthy",
        capabilities={
            "duckdb_version": "1.5.5",
            "extensions": ["httpfs", "iceberg", "postgres_scanner"],
            "protocol_features": FEATURES,
        },
    )
    db.add(agent)
    await db.commit()
    await db.refresh(agent)
    sock = MockWebSocket()
    registry.register(agent.id, sock)  # type: ignore[arg-type]

    async def pick(db, workspace, *, principal_id=None):
        return agent

    monkeypatch.setattr(query_service, "pick_agent_for", pick)
    return agent, sock


def _url(ws, schema="access", table="audit") -> str:
    return f"/workspaces/{ws.slug}/system-catalog/schemas/{schema}/tables/{table}/sample"


def _parquet_bytes() -> bytes:
    with tempfile.NamedTemporaryFile(suffix=".parquet", delete=False) as fh:
        path = fh.name
    try:
        duckdb.connect().execute(
            f"COPY (SELECT 'catalog_grant.create' AS action) TO '{path}' (FORMAT PARQUET)"
        )
        with open(path, "rb") as f:
            return f.read()
    finally:
        os.unlink(path)


async def test_an_admin_gets_the_first_rows(client, db_session, admin, workspace, monkeypatch):
    await _agent(db_session, monkeypatch)
    ran: list[dict] = []

    async def fake_run(db, **kwargs):
        ran.append(kwargs)
        return Query(
            workspace_id=workspace.id,
            sql=kwargs["sql"],
            status="done",
            row_count=1,
            result_path="/results/x.parquet",
            result_schema=[{"name": "action", "type": "VARCHAR"}],
        )

    async def fake_token(db, agent_id):
        return "tok"

    async def fake_proxy(agent, query, range_header=None, *, token=None):
        return httpx.Response(200, content=_parquet_bytes())

    monkeypatch.setattr(query_service, "run_sync_query", fake_run)
    monkeypatch.setattr(query_service, "agent_session_token", fake_token)
    monkeypatch.setattr(query_service, "proxy_rows", fake_proxy)

    resp = await (await _login(client, admin)).get(_url(workspace))

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["rows"] == [{"action": "catalog_grant.create"}]
    assert body["column_schema"] == [{"name": "action", "type": "VARCHAR"}]
    (run,) = ran
    assert run["sql"] == AUDIT_SQL
    # Kept out of History and out of `duckhaven.query.history`, as any preview is.
    assert run["origin"] == "sample"


async def test_it_runs_as_a_query_that_attaches_the_system_catalog(
    client, db_session, admin, workspace, monkeypatch
):
    """Through the same access check and attach as a worksheet's query."""
    _agent_row, sock = await _agent(db_session, monkeypatch)
    real_run = query_service.run_sync_query

    async def quick_run(db, **kwargs):
        # The mock agent never answers: dispatch, then stop waiting.
        return await real_run(db, **kwargs, timeout_s=0.2, poll_interval_s=0.05)

    monkeypatch.setattr(query_service, "run_sync_query", quick_run)

    resp = await (await _login(client, admin)).get(_url(workspace, "query", "history"))

    assert resp.status_code == 504, resp.text
    (frame,) = sock.frames(FrameType.DISPATCH_QUERY)
    assert frame.payload["sql"] == 'SELECT * FROM "duckhaven"."query"."history" LIMIT 20'
    assert frame.payload["catalogs"][-1]["slug"] == "duckhaven"
    assert frame.payload["catalogs"][-1]["kind"] == "system"


async def test_it_is_refused_while_its_database_cannot_be_reached(
    client, db_session, admin, workspace, monkeypatch
):
    _agent_row, sock = await _agent(db_session, monkeypatch)
    monkeypatch.setattr(store, "is_available", lambda: False)

    resp = await (await _login(client, admin)).get(_url(workspace))

    assert resp.status_code == 422, resp.text
    assert resp.json()["error"] == "system_catalog_unavailable"
    assert sock.frames(FrameType.DISPATCH_QUERY) == []


async def test_without_an_agent_it_is_a_503(client, admin, workspace, monkeypatch):
    async def none(db, workspace, *, principal_id=None):
        return None

    monkeypatch.setattr(query_service, "pick_agent_for", none)

    resp = await (await _login(client, admin)).get(_url(workspace))

    assert resp.status_code == 503, resp.text


@pytest.mark.parametrize(("schema", "table"), [("access", "nope"), ("pg_catalog", "pg_roles")])
async def test_a_table_it_does_not_have_is_not_found(
    client, db_session, admin, workspace, monkeypatch, schema, table
):
    _agent_row, sock = await _agent(db_session, monkeypatch)

    resp = await (await _login(client, admin)).get(_url(workspace, schema, table))

    assert resp.status_code == 404
    assert sock.frames(FrameType.DISPATCH_QUERY) == []


async def test_a_member_without_the_permissions_is_refused(client, db_session, workspace):
    member = await _user(db_session, "member@syspreview.local", "user")
    db_session.add(WorkspaceMember(workspace_id=workspace.id, user_id=member.id, role="writer"))
    await db_session.commit()

    resp = await (await _login(client, member)).get(_url(workspace))

    assert resp.status_code == 403


async def test_it_is_absent_when_turned_off(client, admin, workspace, monkeypatch):
    monkeypatch.setattr(settings, "system_catalog_enabled", False)

    resp = await (await _login(client, admin)).get(_url(workspace))

    assert resp.status_code == 404


async def test_it_is_absent_where_a_workspace_catalog_has_its_name(client, db_session, admin):
    ws, _cat = await seed_workspace(
        db_session, user_id=admin.id, slug="legacy", catalog_slug="duckhaven"
    )

    resp = await (await _login(client, admin)).get(_url(ws))

    assert resp.status_code == 404
