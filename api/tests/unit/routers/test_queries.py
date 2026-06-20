import uuid

import pytest_asyncio
from httpx import AsyncClient

from api.models.agent import Agent
from api.models.storage_backend import StorageBackend
from api.models.user import User
from api.models.workspace import Workspace, WorkspaceMember
from api.services.agent_registry import registry
from api.services.auth import hash_password


class MockWebSocket:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send_text(self, text: str) -> None:
        self.sent.append(text)


@pytest_asyncio.fixture
async def user(db_session):
    u = User(
        email="q@queries.local",
        password_hash=hash_password("pw"),
        name="Querier",
        role="user",
    )
    db_session.add(u)
    await db_session.commit()
    await db_session.refresh(u)
    return u


@pytest_asyncio.fixture
async def authed_client(client: AsyncClient, user: User):
    await client.post("/auth/login", json={"email": "q@queries.local", "password": "pw"})
    return client


@pytest_asyncio.fixture
async def workspace(db_session, user: User):
    backend = StorageBackend(
        kind="object_store",
        name="test-store",
        root_uri="/tmp/test",
        created_by=user.id,
    )
    db_session.add(backend)
    await db_session.flush()

    ws = Workspace(slug="test-ws", name="Test WS", storage_backend_id=backend.id)
    db_session.add(ws)
    await db_session.flush()

    member = WorkspaceMember(workspace_id=ws.id, user_id=user.id, role="owner")
    db_session.add(member)
    await db_session.commit()
    await db_session.refresh(ws)
    return ws


@pytest_asyncio.fixture
async def agent(db_session):
    # All backends (object_store is MinIO-backed) require httpfs.
    a = Agent(name="test-agent", status="healthy", capabilities={"extensions": ["httpfs"]})
    db_session.add(a)
    await db_session.commit()
    await db_session.refresh(a)
    return a


@pytest_asyncio.fixture
async def connected_agent(agent: Agent):
    mock_ws = MockWebSocket()
    registry.register(agent.id, mock_ws)  # type: ignore[arg-type]
    yield agent, mock_ws
    registry.unregister(agent.id)


# --- query dispatch ---


async def test_create_query_agent_not_connected(
    authed_client: AsyncClient, workspace: Workspace, agent: Agent
):
    resp = await authed_client.post(
        f"/workspaces/{workspace.slug}/queries",
        json={"sql": "SELECT 1", "agent_id": str(agent.id)},
    )
    assert resp.status_code == 503


async def test_create_query_rejects_disallowed_sql(
    authed_client: AsyncClient, workspace: Workspace, connected_agent
):
    agent, mock_ws = connected_agent
    resp = await authed_client.post(
        f"/workspaces/{workspace.slug}/queries",
        json={"sql": "ATTACH 'evil.db' AS evil", "agent_id": str(agent.id)},
    )
    assert resp.status_code == 422
    body = resp.json()
    assert body["detail"]["error"] == "sql_not_allowed"
    # No frame was sent to the agent.
    assert mock_ws.sent == []


async def test_set_concurrency_command_intercepted(
    authed_client: AsyncClient, workspace: Workspace, connected_agent
):
    """`SET duckhaven_concurrency` is a control command: it is not rejected by the
    SQL guard, sends a SET_CONCURRENCY frame to the agent, and records a done
    query for audit."""
    import json

    from duckhaven_shared.protocol import FrameType

    agent, mock_ws = connected_agent
    resp = await authed_client.post(
        f"/workspaces/{workspace.slug}/queries",
        json={"sql": "SET duckhaven_concurrency = 'single'", "agent_id": str(agent.id)},
    )
    assert resp.status_code == 202
    data = resp.json()
    assert data["status"] == "done"
    assert data["row_count"] == 0

    assert len(mock_ws.sent) == 1
    frame = json.loads(mock_ws.sent[0])
    assert frame["type"] == FrameType.SET_CONCURRENCY
    assert frame["payload"] == {"profile": "single"}


async def test_set_concurrency_invalid_profile_rejected(
    authed_client: AsyncClient, workspace: Workspace, connected_agent
):
    agent, mock_ws = connected_agent
    resp = await authed_client.post(
        f"/workspaces/{workspace.slug}/queries",
        json={"sql": "SET duckhaven_concurrency = 'nope'", "agent_id": str(agent.id)},
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "sql_not_allowed"
    assert mock_ws.sent == []


async def test_create_query_agent_not_found(authed_client: AsyncClient, workspace: Workspace):
    resp = await authed_client.post(
        f"/workspaces/{workspace.slug}/queries",
        json={"sql": "SELECT 1", "agent_id": str(uuid.uuid4())},
    )
    assert resp.status_code == 404


async def test_create_query_dispatches(
    authed_client: AsyncClient, workspace: Workspace, connected_agent, user: User
):
    agent, mock_ws = connected_agent
    resp = await authed_client.post(
        f"/workspaces/{workspace.slug}/queries",
        json={"sql": "SELECT 42", "agent_id": str(agent.id)},
    )
    assert resp.status_code == 202
    data = resp.json()
    # Status stays "queued" until the agent admits the query (it may queue it
    # behind others first) and emits QUERY_PROGRESS.
    assert data["status"] == "queued"
    assert data["sql"] == "SELECT 42"
    # Dispatch records the authenticated user_id on the query (G-D11-a).
    assert data["user_id"] == str(user.id)
    assert len(mock_ws.sent) == 1
    import json

    from duckhaven_shared.protocol import FrameType

    frame = json.loads(mock_ws.sent[0])
    assert frame["type"] == FrameType.DISPATCH_QUERY
    assert frame["payload"]["sql"] == "SELECT 42"
    # Dispatch payload carries the workspace backend descriptor; the agent
    # resolves storage handling from the kind (object_store is MinIO-backed).
    assert frame["payload"]["backend"] == {"kind": "object_store", "root_uri": "/tmp/test"}
    assert frame["payload"]["workspace"] == {"slug": "test-ws", "default_schema": "analytics"}
    assert "storage_credentials" not in frame["payload"]


async def test_dispatch_payload_carries_backend_and_no_credentials(
    authed_client: AsyncClient, db_session, user: User, connected_agent
):
    """The dispatch frame carries the backend descriptor and the workspace
    slug, but no storage credentials or catalog endpoint — the agent attaches
    Polaris from its own config and Polaris vends storage creds on attach."""
    import json

    from api.models.storage_backend import StorageBackend
    from api.models.workspace import Workspace, WorkspaceMember

    agent, mock_ws = connected_agent
    agent.capabilities = {"extensions": ["httpfs"]}  # required for s3 (G-D17-b)
    db_session.add(agent)

    sb = StorageBackend(
        kind="s3", name="s3-store", root_uri="s3://bucket/prefix", created_by=user.id
    )
    db_session.add(sb)
    await db_session.flush()
    ws = Workspace(slug="s3-ws", name="S3 WS", storage_backend_id=sb.id)
    db_session.add(ws)
    await db_session.flush()
    db_session.add(WorkspaceMember(workspace_id=ws.id, user_id=user.id, role="owner"))
    await db_session.commit()

    resp = await authed_client.post(
        "/workspaces/s3-ws/queries",
        json={"sql": "SELECT 1", "agent_id": str(agent.id)},
    )
    assert resp.status_code == 202, resp.text

    payload = json.loads(mock_ws.sent[-1])["payload"]
    assert payload["backend"] == {"kind": "s3", "root_uri": "s3://bucket/prefix"}
    assert payload["workspace"] == {"slug": "s3-ws", "default_schema": "analytics"}
    assert "storage_credentials" not in payload
    assert "catalog" not in payload


async def test_dispatch_rejects_agent_missing_extension(
    authed_client: AsyncClient, db_session, user: User, connected_agent
):
    """A cloud-backed workspace cannot dispatch to an agent that lacks the
    required DuckDB extension; the query is never created or sent (G-D17-b)."""
    from api.models.storage_backend import StorageBackend
    from api.models.workspace import Workspace, WorkspaceMember

    agent, mock_ws = connected_agent
    agent.capabilities = {"extensions": ["httpfs", "iceberg"]}  # no azure
    db_session.add(agent)

    sb = StorageBackend(
        kind="adls_gen2", name="adls", root_uri="abfss://c@acct/", created_by=user.id
    )
    db_session.add(sb)
    await db_session.flush()
    ws = Workspace(slug="adls-ws", name="ADLS WS", storage_backend_id=sb.id)
    db_session.add(ws)
    await db_session.flush()
    db_session.add(WorkspaceMember(workspace_id=ws.id, user_id=user.id, role="owner"))
    await db_session.commit()

    resp = await authed_client.post(
        "/workspaces/adls-ws/queries",
        json={"sql": "SELECT 1", "agent_id": str(agent.id)},
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "agent_incompatible"
    assert mock_ws.sent == []


# --- workspace query history ---


async def test_list_workspace_queries_scoped_and_ordered(
    authed_client: AsyncClient, workspace: Workspace, agent: Agent, db_session, user: User
):
    """History lists only this workspace's queries, newest first, and excludes
    sample-origin internal queries."""
    from datetime import UTC, datetime, timedelta

    from api.models.query import Query

    now = datetime.now(UTC)
    # Two real queries in the workspace + one internal sample preview.
    db_session.add_all(
        [
            Query(
                workspace_id=workspace.id,
                agent_id=agent.id,
                sql="SELECT 'older'",
                status="done",
                started_at=now - timedelta(minutes=5),
            ),
            Query(
                workspace_id=workspace.id,
                agent_id=agent.id,
                sql="SELECT 'newer'",
                status="done",
                started_at=now,
            ),
            Query(
                workspace_id=workspace.id,
                agent_id=agent.id,
                sql="SELECT 'sample'",
                status="done",
                origin="sample",
                started_at=now - timedelta(minutes=1),
            ),
        ]
    )
    # A query in a different workspace must not leak in.
    other_ws = Workspace(
        slug="other-ws", name="Other", storage_backend_id=workspace.storage_backend_id
    )
    db_session.add(other_ws)
    await db_session.flush()
    db_session.add(
        Query(
            workspace_id=other_ws.id,
            agent_id=agent.id,
            sql="SELECT 'foreign'",
            status="done",
            started_at=now,
        )
    )
    await db_session.commit()

    resp = await authed_client.get(f"/workspaces/{workspace.slug}/queries")
    assert resp.status_code == 200
    rows = resp.json()
    sqls = [r["sql"] for r in rows]
    assert sqls == ["SELECT 'newer'", "SELECT 'older'"]  # newest first, sample excluded


async def test_list_workspace_queries_non_member_forbidden(
    client: AsyncClient, workspace: Workspace, db_session
):
    """A user who is not a member of the workspace cannot read its history."""
    outsider = User(
        email="outsider@queries.local",
        password_hash=hash_password("pw"),
        name="Outsider",
        role="user",
    )
    db_session.add(outsider)
    await db_session.commit()

    await client.post("/auth/login", json={"email": "outsider@queries.local", "password": "pw"})
    resp = await client.get(f"/workspaces/{workspace.slug}/queries")
    assert resp.status_code == 403


async def test_list_queries_all_workspaces_forbidden_for_non_admin(
    authed_client: AsyncClient, workspace: Workspace
):
    """The cross-workspace view is admin-only; a member is rejected with 403."""
    resp = await authed_client.get(f"/workspaces/{workspace.slug}/queries?all_workspaces=true")
    assert resp.status_code == 403


async def test_list_queries_user_filter_forbidden_for_non_admin(
    authed_client: AsyncClient, workspace: Workspace, user: User
):
    """The audit filters are admin-only, even within one's own workspace."""
    resp = await authed_client.get(f"/workspaces/{workspace.slug}/queries?user_id={user.id}")
    assert resp.status_code == 403


async def test_admin_can_list_all_workspaces_and_filter_by_user(
    client: AsyncClient, workspace: Workspace, agent: Agent, db_session, user: User
):
    """An admin can read across workspaces and filter by user (the merged audit
    view). The admin need not be a member of those workspaces."""
    from api.models.query import Query

    admin = User(
        email="admin@queries.local",
        password_hash=hash_password("pw"),
        name="Admin",
        role="admin",
    )
    db_session.add(admin)
    await db_session.flush()

    other_ws = Workspace(
        slug="other-ws", name="Other", storage_backend_id=workspace.storage_backend_id
    )
    db_session.add(other_ws)
    await db_session.flush()

    db_session.add_all(
        [
            Query(
                workspace_id=workspace.id,
                agent_id=agent.id,
                user_id=user.id,
                sql="SELECT 'in_ws'",
                status="done",
            ),
            Query(
                workspace_id=other_ws.id,
                agent_id=agent.id,
                user_id=admin.id,
                sql="SELECT 'in_other'",
                status="done",
            ),
        ]
    )
    await db_session.commit()

    await client.post("/auth/login", json={"email": "admin@queries.local", "password": "pw"})

    # Cross-workspace: both queries are visible.
    resp = await client.get(f"/workspaces/{workspace.slug}/queries?all_workspaces=true")
    assert resp.status_code == 200
    assert {r["sql"] for r in resp.json()} == {"SELECT 'in_ws'", "SELECT 'in_other'"}

    # Filtered by user: only that user's query, regardless of workspace.
    resp = await client.get(
        f"/workspaces/{workspace.slug}/queries?all_workspaces=true&user_id={user.id}"
    )
    assert resp.status_code == 200
    assert [r["sql"] for r in resp.json()] == ["SELECT 'in_ws'"]


# --- query status ---


async def test_get_query(
    authed_client: AsyncClient, workspace: Workspace, connected_agent, db_session
):
    agent, _ = connected_agent
    resp = await authed_client.post(
        f"/workspaces/{workspace.slug}/queries",
        json={"sql": "SELECT 1", "agent_id": str(agent.id)},
    )
    query_id = resp.json()["id"]

    resp = await authed_client.get(f"/queries/{query_id}")
    assert resp.status_code == 200
    assert resp.json()["id"] == query_id


async def test_get_query_not_found(authed_client: AsyncClient):
    resp = await authed_client.get(f"/queries/{uuid.uuid4()}")
    assert resp.status_code == 404


async def test_get_query_profile_returns_persisted(
    authed_client: AsyncClient, workspace: Workspace, agent: Agent, db_session
):
    from api.models.query import Query

    profile = {
        "summary": {"latency_ms": 5.0, "spill_bytes": 0},
        "tree": {"type": "PROJECTION", "children": []},
    }
    query = Query(
        workspace_id=workspace.id,
        agent_id=agent.id,
        sql="SELECT 1",
        status="done",
        profile=profile,
    )
    db_session.add(query)
    await db_session.commit()
    await db_session.refresh(query)

    resp = await authed_client.get(f"/queries/{query.id}/profile")
    assert resp.status_code == 200
    assert resp.json() == profile


async def test_get_query_profile_null_when_absent(
    authed_client: AsyncClient, workspace: Workspace, connected_agent
):
    agent, _ = connected_agent
    resp = await authed_client.post(
        f"/workspaces/{workspace.slug}/queries",
        json={"sql": "SELECT 1", "agent_id": str(agent.id)},
    )
    query_id = resp.json()["id"]

    resp = await authed_client.get(f"/queries/{query_id}/profile")
    assert resp.status_code == 200
    assert resp.json() is None


async def test_get_query_profile_not_found(authed_client: AsyncClient):
    resp = await authed_client.get(f"/queries/{uuid.uuid4()}/profile")
    assert resp.status_code == 404


async def test_query_progress_persisted_and_exposed(
    authed_client: AsyncClient, workspace: Workspace, agent: Agent, db_session
):
    """A QUERY_PROGRESS frame persists progress, exposed by GET /queries/{id} (G-D16-b)."""
    from datetime import UTC, datetime

    from api.models.query import Query
    from api.services import query as query_service
    from duckhaven_shared.protocol import Frame, FrameType

    query = Query(
        workspace_id=workspace.id,
        agent_id=agent.id,
        sql="SELECT 1",
        status="running",
        started_at=datetime.now(UTC),
    )
    db_session.add(query)
    await db_session.commit()
    await db_session.refresh(query)

    await query_service.handle_agent_frame(
        db_session,
        Frame(
            type=FrameType.QUERY_PROGRESS,
            payload={"query_id": str(query.id), "stage": "scanning", "pct": 42},
        ),
    )

    resp = await authed_client.get(f"/queries/{query.id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "running"
    assert body["progress"] == {"stage": "scanning", "pct": 42}


async def test_query_done_persists_result_bytes(
    authed_client: AsyncClient, workspace: Workspace, agent: Agent, db_session
):
    """A QUERY_DONE frame's result_bytes persists and is exposed by GET /queries/{id}."""
    from datetime import UTC, datetime

    from api.models.query import Query
    from api.services import query as query_service
    from duckhaven_shared.protocol import Frame, FrameType

    query = Query(
        workspace_id=workspace.id,
        agent_id=agent.id,
        sql="SELECT 1",
        status="running",
        started_at=datetime.now(UTC),
    )
    db_session.add(query)
    await db_session.commit()
    await db_session.refresh(query)

    await query_service.handle_agent_frame(
        db_session,
        Frame(
            type=FrameType.QUERY_DONE,
            payload={
                "query_id": str(query.id),
                "status": "done",
                "row_count": 3,
                "duration_ms": 12,
                "result_bytes": 4096,
                "result_path": "/tmp/x.parquet",
            },
        ),
    )

    resp = await authed_client.get(f"/queries/{query.id}")
    assert resp.status_code == 200
    assert resp.json()["result_bytes"] == 4096


# --- cancel ---


async def test_cancel_query(authed_client: AsyncClient, workspace: Workspace, connected_agent):
    agent, mock_ws = connected_agent
    resp = await authed_client.post(
        f"/workspaces/{workspace.slug}/queries",
        json={"sql": "SELECT 1", "agent_id": str(agent.id)},
    )
    query_id = resp.json()["id"]

    resp = await authed_client.delete(f"/queries/{query_id}")
    assert resp.status_code == 204

    resp = await authed_client.get(f"/queries/{query_id}")
    assert resp.json()["status"] == "cancelled"


# --- rows ---


async def test_rows_query_not_done(
    authed_client: AsyncClient, workspace: Workspace, connected_agent
):
    agent, _ = connected_agent
    resp = await authed_client.post(
        f"/workspaces/{workspace.slug}/queries",
        json={"sql": "SELECT 1", "agent_id": str(agent.id)},
    )
    query_id = resp.json()["id"]

    resp = await authed_client.get(f"/queries/{query_id}/rows")
    assert resp.status_code == 409


async def test_rows_done_without_result_returns_empty_page(
    authed_client: AsyncClient, workspace: Workspace, agent: Agent, db_session
):
    """A DDL/DML query finishes done with no result file; rows returns an empty
    page rather than 404."""
    from datetime import UTC, datetime

    from api.models.query import Query

    query = Query(
        workspace_id=workspace.id,
        agent_id=agent.id,
        sql="CREATE TABLE t (x INT)",
        status="done",
        result_path=None,
        started_at=datetime.now(UTC),
    )
    db_session.add(query)
    await db_session.commit()
    await db_session.refresh(query)

    resp = await authed_client.get(f"/queries/{query.id}/rows")
    assert resp.status_code == 200
    body = resp.json()
    assert body["rows"] == []
    assert body["columns"] == []
    assert body["total"] == 0


# --- saved queries ---


async def test_list_saved_queries_empty(authed_client: AsyncClient, workspace: Workspace):
    resp = await authed_client.get(f"/workspaces/{workspace.slug}/saved-queries")
    assert resp.status_code == 200
    assert resp.json() == []


async def test_create_saved_query(authed_client: AsyncClient, workspace: Workspace):
    resp = await authed_client.post(
        f"/workspaces/{workspace.slug}/saved-queries",
        json={"name": "My Query", "sql": "SELECT 1"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "My Query"
    assert data["sql"] == "SELECT 1"


async def test_get_query_rows_agent_no_result_host(
    authed_client: AsyncClient, workspace: Workspace, agent: Agent, db_session
):
    from datetime import UTC, datetime

    from api.models.query import Query

    query = Query(
        workspace_id=workspace.id,
        agent_id=agent.id,
        sql="SELECT 1",
        status="done",
        result_path="/tmp/fake.parquet",
        started_at=datetime.now(UTC),
    )
    db_session.add(query)
    await db_session.commit()
    await db_session.refresh(query)

    resp = await authed_client.get(f"/queries/{query.id}/rows")
    assert resp.status_code == 503


async def test_get_query_rows_resolves_agent_bearer(
    authed_client: AsyncClient, workspace: Workspace, agent: Agent, db_session, monkeypatch
):
    """The rows handler resolves the agent's session token, passes it to the
    proxy, and decodes the Parquet result into JSON RowsPageOut (G-D16-a)."""
    import os
    import tempfile
    from datetime import UTC, datetime

    import duckdb
    import httpx

    from api.models.query import Query
    from api.models.user import Credential
    from api.services import query as query_service

    def _parquet_bytes() -> bytes:
        with tempfile.NamedTemporaryFile(suffix=".parquet", delete=False) as fh:
            path = fh.name
        try:
            duckdb.connect().execute(f"COPY (SELECT 1 AS a, 'x' AS b) TO '{path}' (FORMAT PARQUET)")
            with open(path, "rb") as f:
                return f.read()
        finally:
            os.unlink(path)

    agent.result_host = "127.0.0.1"
    agent.result_port = 8001
    db_session.add(agent)

    session_token = "agent-session-tok"
    db_session.add(
        Credential(user_id=None, agent_id=agent.id, kind="agent_session", token=session_token)
    )

    query = Query(
        workspace_id=workspace.id,
        agent_id=agent.id,
        sql="SELECT 1",
        status="done",
        row_count=1,
        result_path="/var/duckhaven-agent/results/x.parquet",
        started_at=datetime.now(UTC),
    )
    db_session.add(query)
    await db_session.commit()
    await db_session.refresh(query)

    captured: dict[str, object] = {}

    async def fake_proxy(agent_arg, query_arg, *, row_offset=None, row_limit=None, token=None):
        captured["token"] = token
        captured["row_offset"] = row_offset
        captured["row_limit"] = row_limit
        return httpx.Response(
            200, content=_parquet_bytes(), headers={"X-DH-Row-Offset": str(row_offset)}
        )

    monkeypatch.setattr(query_service, "proxy_rows", fake_proxy)

    resp = await authed_client.get(f"/queries/{query.id}/rows")
    assert resp.status_code == 200
    assert captured["token"] == session_token
    assert captured["row_offset"] == 0
    body = resp.json()
    assert body["columns"] == ["a", "b"]
    assert body["rows"] == [{"a": 1, "b": "x"}]
    assert body["total"] == 1
    assert body["cursor"] is None


def _make_parquet(select_sql: str) -> bytes:
    import os
    import tempfile

    import duckdb

    with tempfile.NamedTemporaryFile(suffix=".parquet", delete=False) as fh:
        path = fh.name
    try:
        duckdb.connect().execute(f"COPY ({select_sql}) TO '{path}' (FORMAT PARQUET)")
        with open(path, "rb") as f:
            return f.read()
    finally:
        os.unlink(path)


async def _done_query_with_agent(db_session, workspace, agent, *, row_count: int):
    from datetime import UTC, datetime

    from api.models.query import Query
    from api.models.user import Credential

    agent.result_host = "127.0.0.1"
    agent.result_port = 8001
    db_session.add(agent)
    db_session.add(Credential(user_id=None, agent_id=agent.id, kind="agent_session", token="tok"))
    query = Query(
        workspace_id=workspace.id,
        agent_id=agent.id,
        sql="SELECT 1",
        status="done",
        row_count=row_count,
        result_path="/var/duckhaven-agent/results/x.parquet",
        started_at=datetime.now(UTC),
    )
    db_session.add(query)
    await db_session.commit()
    await db_session.refresh(query)
    return query


async def test_get_query_rows_far_page_requests_window_only(
    authed_client: AsyncClient, workspace: Workspace, agent: Agent, db_session, monkeypatch
):
    """A far-offset page passes the window to the agent and decodes the returned
    slice at offset 0 — never pulling the whole result through the decoder."""
    import httpx

    from api.services import query as query_service

    query = await _done_query_with_agent(db_session, workspace, agent, row_count=10_000)

    captured: dict[str, object] = {}

    async def fake_proxy(agent_arg, query_arg, *, row_offset=None, row_limit=None, token=None):
        captured["row_offset"] = row_offset
        captured["row_limit"] = row_limit
        # Simulate the agent slicing rows [offset, offset+limit).
        sliced = _make_parquet(
            f"SELECT i AS n FROM range({row_offset}, {row_offset + row_limit}) t(i)"
        )
        return httpx.Response(200, content=sliced, headers={"X-DH-Row-Offset": str(row_offset)})

    monkeypatch.setattr(query_service, "proxy_rows", fake_proxy)

    resp = await authed_client.get(f"/queries/{query.id}/rows?limit=5&cursor=9000")
    assert resp.status_code == 200
    assert captured["row_offset"] == 9000
    assert captured["row_limit"] == 5
    body = resp.json()
    assert [r["n"] for r in body["rows"]] == [9000, 9001, 9002, 9003, 9004]
    assert body["total"] == 10_000
    assert body["cursor"] == "9005"


async def test_get_query_rows_fallback_when_agent_does_not_slice(
    authed_client: AsyncClient, workspace: Workspace, agent: Agent, db_session, monkeypatch
):
    """An older agent ignores the window params and returns the whole file (no
    X-DH-Row-Offset); the control plane still pages correctly via local offset."""
    import httpx

    from api.services import query as query_service

    query = await _done_query_with_agent(db_session, workspace, agent, row_count=10)

    async def fake_proxy(agent_arg, query_arg, *, row_offset=None, row_limit=None, token=None):
        full = _make_parquet("SELECT i AS n FROM range(10) t(i)")
        return httpx.Response(200, content=full)  # no slice header

    monkeypatch.setattr(query_service, "proxy_rows", fake_proxy)

    resp = await authed_client.get(f"/queries/{query.id}/rows?limit=3&cursor=4")
    assert resp.status_code == 200
    body = resp.json()
    assert [r["n"] for r in body["rows"]] == [4, 5, 6]


async def test_get_query_rows_swept_result_returns_410(
    authed_client: AsyncClient, workspace: Workspace, agent: Agent, db_session, monkeypatch
):
    import httpx

    from api.services import query as query_service

    query = await _done_query_with_agent(db_session, workspace, agent, row_count=10)

    async def fake_proxy(agent_arg, query_arg, *, row_offset=None, row_limit=None, token=None):
        return httpx.Response(404, json={"detail": "Not found"})

    monkeypatch.setattr(query_service, "proxy_rows", fake_proxy)

    resp = await authed_client.get(f"/queries/{query.id}/rows")
    assert resp.status_code == 410


async def test_proxy_rows_sets_bearer_and_window(monkeypatch):
    """proxy_rows attaches the agent bearer and forwards the row window params."""
    from datetime import UTC, datetime

    import httpx

    from api.models.agent import Agent
    from api.models.query import Query
    from api.services import query as query_service

    seen: dict[str, str | None] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("Authorization")
        seen["row_offset"] = request.url.params.get("row_offset")
        seen["row_limit"] = request.url.params.get("row_limit")
        return httpx.Response(200, content=b"x")

    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        query_service.httpx, "AsyncClient", lambda *a, **kw: real_client(transport=transport)
    )

    agent = Agent(name="a", status="healthy", result_host="127.0.0.1", result_port=8001)
    query = Query(
        workspace_id=uuid.uuid4(), sql="SELECT 1", status="done", started_at=datetime.now(UTC)
    )
    query.id = uuid.uuid4()

    resp = await query_service.proxy_rows(agent, query, row_offset=200, row_limit=50, token="abc")
    assert resp.status_code == 200
    assert seen["auth"] == "Bearer abc"
    assert seen["row_offset"] == "200"
    assert seen["row_limit"] == "50"


async def test_cancel_query_agent_not_connected(
    authed_client: AsyncClient, workspace: Workspace, agent: Agent, db_session
):
    from datetime import UTC, datetime

    from api.models.query import Query

    query = Query(
        workspace_id=workspace.id,
        agent_id=agent.id,
        sql="SELECT 1",
        status="running",
        started_at=datetime.now(UTC),
    )
    db_session.add(query)
    await db_session.commit()
    await db_session.refresh(query)

    resp = await authed_client.delete(f"/queries/{query.id}")
    assert resp.status_code == 204

    resp = await authed_client.get(f"/queries/{query.id}")
    assert resp.json()["status"] == "cancelled"


async def test_create_saved_query_reader_role_rejected(
    authed_client: AsyncClient, workspace: Workspace, db_session
):
    from api.models.user import User
    from api.models.workspace import WorkspaceMember
    from api.services.auth import hash_password

    reader = User(
        email="reader@queries.local",
        password_hash=hash_password("pw"),
        name="Reader",
        role="user",
    )
    db_session.add(reader)
    await db_session.flush()
    member = WorkspaceMember(workspace_id=workspace.id, user_id=reader.id, role="reader")
    db_session.add(member)
    await db_session.commit()

    await authed_client.post("/auth/logout")
    await authed_client.post(
        "/auth/login", json={"email": "reader@queries.local", "password": "pw"}
    )

    resp = await authed_client.post(
        f"/workspaces/{workspace.slug}/saved-queries",
        json={"name": "Test", "sql": "SELECT 1"},
    )
    assert resp.status_code == 403
