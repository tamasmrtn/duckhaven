import uuid

import pytest_asyncio
from conftest import seed_workspace
from httpx import AsyncClient

from api.models.agent import Agent
from api.models.agent_grant import AgentGrant
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
    ws, _catalog = await seed_workspace(db_session, user_id=user.id)
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
    assert body["error"] == "sql_not_allowed"
    # No frame was sent to the agent.
    assert mock_ws.sent == []


async def test_set_concurrency_command_intercepted(
    authed_client: AsyncClient, workspace: Workspace, connected_agent, db_session, user: User
):
    """`SET duckhaven_concurrency` is a control command: it is not rejected by the
    SQL guard, sends a SET_CONCURRENCY frame to the agent, and records a done
    query for audit."""
    import json

    from duckhaven_shared.protocol import FrameType

    agent, mock_ws = connected_agent
    # Retuning admission affects every query on the agent, so it needs `operate`.
    db_session.add(AgentGrant(agent_id=agent.id, user_id=user.id, tier="operate"))
    await db_session.commit()
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
    assert resp.json()["error"] == "sql_not_allowed"
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
    # Dispatch payload carries the workspace's catalog descriptors (each with its
    # backend) and the active catalog; the agent attaches them all.
    assert frame["payload"]["active_catalog"] == "test_ws"
    assert frame["payload"]["catalogs"] == [
        {
            "slug": "test_ws",
            "polaris_name": "test-ws",
            "backend": {"kind": "object_store", "root_uri": "/tmp/test"},
            "default_schema": "analytics",
        }
    ]
    assert "storage_credentials" not in frame["payload"]


async def test_dispatch_payload_carries_backend_and_no_credentials(
    authed_client: AsyncClient, db_session, user: User, connected_agent
):
    """The dispatch frame carries the catalog descriptors (each with its backend)
    but no storage credentials or catalog endpoint — the agent attaches Polaris
    from its own config and Polaris vends storage creds on attach."""
    import json

    agent, mock_ws = connected_agent
    agent.capabilities = {"extensions": ["httpfs"]}  # required for s3 (G-D17-b)
    db_session.add(agent)

    await seed_workspace(
        db_session,
        user_id=user.id,
        slug="s3-ws",
        name="S3 WS",
        backend_kind="s3",
        catalog_slug="s3_cat",
    )

    resp = await authed_client.post(
        "/workspaces/s3-ws/queries",
        json={"sql": "SELECT 1", "agent_id": str(agent.id)},
    )
    assert resp.status_code == 202, resp.text

    payload = json.loads(mock_ws.sent[-1])["payload"]
    assert payload["active_catalog"] == "s3_cat"
    assert payload["catalogs"] == [
        {
            "slug": "s3_cat",
            "polaris_name": "s3-ws",
            "backend": {"kind": "s3", "root_uri": "/tmp/test"},
            "default_schema": "analytics",
        }
    ]
    assert "storage_credentials" not in payload


async def test_dispatch_rejects_agent_missing_extension(
    authed_client: AsyncClient, db_session, user: User, connected_agent
):
    """A cloud-backed workspace cannot dispatch to an agent that lacks the
    required DuckDB extension; the query is never created or sent (G-D17-b)."""
    agent, mock_ws = connected_agent
    agent.capabilities = {"extensions": ["httpfs", "iceberg"]}  # no azure
    db_session.add(agent)

    await seed_workspace(
        db_session,
        user_id=user.id,
        slug="adls-ws",
        name="ADLS WS",
        backend_kind="adls_gen2",
        catalog_slug="adls_cat",
    )

    resp = await authed_client.post(
        "/workspaces/adls-ws/queries",
        json={"sql": "SELECT 1", "agent_id": str(agent.id)},
    )
    assert resp.status_code == 422
    assert resp.json()["error"] == "agent_incompatible"
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
    other_ws, _ = await seed_workspace(
        db_session, user_id=user.id, slug="other-ws", name="Other", role=None
    )
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
    rows = resp.json()["items"]
    sqls = [r["sql"] for r in rows]
    assert sqls == ["SELECT 'newer'", "SELECT 'older'"]  # newest first, sample excluded


async def test_list_workspace_queries_resolves_user_name(
    authed_client: AsyncClient, workspace: Workspace, agent: Agent, db_session, user: User
):
    """History resolves user_id to a display name (the User column) and leaves it
    null for internal runs with no user."""
    from datetime import UTC, datetime

    from api.models.query import Query

    now = datetime.now(UTC)
    db_session.add_all(
        [
            Query(
                workspace_id=workspace.id,
                agent_id=agent.id,
                user_id=user.id,
                sql="SELECT 'by user'",
                status="done",
                started_at=now,
            ),
            Query(
                workspace_id=workspace.id,
                agent_id=agent.id,
                user_id=None,
                sql="SELECT 'no user'",
                status="done",
                started_at=now,
            ),
        ]
    )
    await db_session.commit()

    resp = await authed_client.get(f"/workspaces/{workspace.slug}/queries")
    assert resp.status_code == 200
    by_sql = {r["sql"]: r for r in resp.json()["items"]}
    assert by_sql["SELECT 'by user'"]["user_name"] == "Querier"
    assert by_sql["SELECT 'no user'"]["user_name"] is None


async def test_list_workspace_queries_filters_by_origin_and_session(
    authed_client: AsyncClient, workspace: Workspace, agent: Agent, db_session, user: User
):
    """A member may narrow History to a kind of run, or to one session's
    statements. These reveal nothing they could not already see in this list, so
    unlike the cross-principal filters they are not admin-gated."""
    from datetime import UTC, datetime

    from api.models.query import Query
    from api.models.sql_session import SqlSession

    session = SqlSession(workspace_id=workspace.id, agent_id=agent.id, user_id=user.id)
    other_session = SqlSession(workspace_id=workspace.id, agent_id=agent.id, user_id=user.id)
    db_session.add_all([session, other_session])
    await db_session.flush()
    now = datetime.now(UTC)
    db_session.add_all(
        [
            Query(
                workspace_id=workspace.id,
                agent_id=agent.id,
                sql="SELECT 'interactive'",
                status="done",
                started_at=now,
            ),
            Query(
                workspace_id=workspace.id,
                agent_id=agent.id,
                sql="SELECT 'mine'",
                status="done",
                origin="session",
                session_id=session.id,
                started_at=now,
            ),
            Query(
                workspace_id=workspace.id,
                agent_id=agent.id,
                sql="SELECT 'theirs'",
                status="done",
                origin="session",
                session_id=other_session.id,
                started_at=now,
            ),
        ]
    )
    await db_session.commit()

    base = f"/workspaces/{workspace.slug}/queries"

    resp = await authed_client.get(base, params={"origin": "session"})
    assert resp.status_code == 200, resp.text
    assert sorted(r["sql"] for r in resp.json()["items"]) == ["SELECT 'mine'", "SELECT 'theirs'"]

    # Interactive runs carry a null origin; the filter spells that case out.
    resp = await authed_client.get(base, params={"origin": "interactive"})
    assert [r["sql"] for r in resp.json()["items"]] == ["SELECT 'interactive'"]

    resp = await authed_client.get(base, params={"session_id": str(session.id)})
    rows = resp.json()["items"]
    assert [r["sql"] for r in rows] == ["SELECT 'mine'"]
    # History needs the id on the wire to link a statement to its session.
    assert rows[0]["session_id"] == str(session.id)


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


async def test_list_queries_self_filter_allowed_for_non_admin(
    authed_client: AsyncClient, workspace: Workspace, user: User
):
    """Narrowing History to *yourself* is not a cross-principal filter.

    It shows a subset of what the unfiltered list already showed this caller, so
    it is open to any member — and it has to be, because it is how the default
    "my queries" view scopes itself.
    """
    resp = await authed_client.get(f"/workspaces/{workspace.slug}/queries?user_id={user.id}")
    assert resp.status_code == 200, resp.text


async def test_list_queries_other_user_filter_forbidden_for_non_admin(
    authed_client: AsyncClient, workspace: Workspace, db_session
):
    """Filtering to someone *else* stays admin-only, own workspace or not."""
    other = User(
        email="someone-else@queries.local",
        password_hash=hash_password("pw"),
        name="Someone Else",
        role="user",
    )
    db_session.add(other)
    await db_session.commit()

    resp = await authed_client.get(f"/workspaces/{workspace.slug}/queries?user_id={other.id}")
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

    other_ws, _ = await seed_workspace(
        db_session, user_id=user.id, slug="other-ws", name="Other", role=None
    )

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
    assert {r["sql"] for r in resp.json()["items"]} == {"SELECT 'in_ws'", "SELECT 'in_other'"}

    # Filtered by user: only that user's query, regardless of workspace.
    resp = await client.get(
        f"/workspaces/{workspace.slug}/queries?all_workspaces=true&user_id={user.id}"
    )
    assert resp.status_code == 200
    assert [r["sql"] for r in resp.json()["items"]] == ["SELECT 'in_ws'"]


async def test_list_queries_agent_filter_allowed_for_non_admin_scoped_to_own_workspace(
    authed_client: AsyncClient, workspace: Workspace, agent: Agent, db_session, user: User
):
    """Unlike user_id/since/until, agent_id is open to any member — it reveals
    nothing they could not already see in their own workspace's history."""
    from api.models.query import Query

    other_ws, _ = await seed_workspace(
        db_session, user_id=user.id, slug="other-ws", name="Other", role=None
    )
    db_session.add_all(
        [
            Query(workspace_id=workspace.id, agent_id=agent.id, sql="SELECT 'mine'", status="done"),
            Query(
                workspace_id=other_ws.id, agent_id=agent.id, sql="SELECT 'foreign'", status="done"
            ),
        ]
    )
    await db_session.commit()

    resp = await authed_client.get(f"/workspaces/{workspace.slug}/queries?agent_id={agent.id}")
    assert resp.status_code == 200
    assert [r["sql"] for r in resp.json()["items"]] == ["SELECT 'mine'"]


async def test_list_queries_agent_filter_with_all_workspaces_forbidden_for_non_admin(
    authed_client: AsyncClient, workspace: Workspace, agent: Agent
):
    """agent_id alone does not unlock the cross-workspace view — all_workspaces
    still requires admin even when combined with an otherwise-open filter."""
    resp = await authed_client.get(
        f"/workspaces/{workspace.slug}/queries?agent_id={agent.id}&all_workspaces=true"
    )
    assert resp.status_code == 403


async def test_admin_can_filter_by_agent_across_workspaces(
    client: AsyncClient, workspace: Workspace, agent: Agent, db_session, user: User
):
    """An admin combining all_workspaces with agent_id still narrows correctly
    (regression for splitting agent_id out of the admin-only gate)."""
    from api.models.query import Query

    admin = User(
        email="agent-admin@queries.local",
        password_hash=hash_password("pw"),
        name="Admin",
        role="admin",
    )
    db_session.add(admin)
    other_agent = Agent(
        name="other-agent", status="healthy", capabilities={"extensions": ["httpfs"]}
    )
    db_session.add(other_agent)
    await db_session.flush()

    other_ws, _ = await seed_workspace(
        db_session, user_id=user.id, slug="other-ws", name="Other", role=None
    )
    db_session.add_all(
        [
            Query(
                workspace_id=workspace.id, agent_id=agent.id, sql="SELECT 'target'", status="done"
            ),
            Query(
                workspace_id=other_ws.id,
                agent_id=other_agent.id,
                sql="SELECT 'other_agent'",
                status="done",
            ),
        ]
    )
    await db_session.commit()

    await client.post("/auth/login", json={"email": "agent-admin@queries.local", "password": "pw"})

    resp = await client.get(
        f"/workspaces/{workspace.slug}/queries?all_workspaces=true&agent_id={agent.id}"
    )
    assert resp.status_code == 200
    assert [r["sql"] for r in resp.json()["items"]] == ["SELECT 'target'"]


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
    # No result grid, so no types to report.
    assert body["column_schema"] is None


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


async def _done_query_with_agent(
    db_session, workspace, agent, *, row_count: int, result_schema: list | None = None
):
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
        result_schema=result_schema,
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


async def test_get_query_rows_reports_column_schema(
    authed_client: AsyncClient, workspace: Workspace, agent: Agent, db_session, monkeypatch
):
    """The types the agent reported are surfaced verbatim, in DuckDB's spelling,
    alongside the unchanged names-only `columns` list."""
    import httpx

    from api.services import query as query_service

    schema = [
        {"name": "ts", "type": "TIMESTAMP WITH TIME ZONE"},
        {"name": "amt", "type": "DECIMAL(38,10)"},
    ]
    query = await _done_query_with_agent(
        db_session, workspace, agent, row_count=1, result_schema=schema
    )

    async def fake_proxy(agent_arg, query_arg, *, row_offset=None, row_limit=None, token=None):
        content = _make_parquet(
            "SELECT TIMESTAMPTZ '2026-01-02 03:04:05+00' AS ts, 1.5::DECIMAL(38,10) AS amt"
        )
        return httpx.Response(200, content=content, headers={"X-DH-Row-Offset": "0"})

    monkeypatch.setattr(query_service, "proxy_rows", fake_proxy)

    resp = await authed_client.get(f"/queries/{query.id}/rows")
    assert resp.status_code == 200
    body = resp.json()
    assert body["column_schema"] == schema
    assert body["columns"] == ["ts", "amt"]


async def test_get_query_from_older_agent_omits_column_schema(
    authed_client: AsyncClient, workspace: Workspace, agent: Agent, db_session, monkeypatch
):
    """Backward compatibility: an agent that never reported a schema leaves the
    page exactly as it was, plus a null `column_schema`. The API does not derive
    one from the Parquet, which is lossy (HUGEINT -> DOUBLE, ENUM -> VARCHAR)."""
    import httpx

    from api.services import query as query_service

    query = await _done_query_with_agent(db_session, workspace, agent, row_count=1)

    async def fake_proxy(agent_arg, query_arg, *, row_offset=None, row_limit=None, token=None):
        content = _make_parquet("SELECT 123456789012345678901::HUGEINT AS h")
        return httpx.Response(200, content=content, headers={"X-DH-Row-Offset": "0"})

    monkeypatch.setattr(query_service, "proxy_rows", fake_proxy)

    resp = await authed_client.get(f"/queries/{query.id}/rows")
    assert resp.status_code == 200
    body = resp.json()
    assert body["column_schema"] is None
    # Every pre-existing key is untouched — the only change is the added key.
    assert set(body) == {"rows", "columns", "cursor", "total", "column_schema"}
    assert body["columns"] == ["h"]
    assert body["total"] == 1
    assert body["cursor"] is None

    # And the same schema is (absent) on the status endpoint.
    status_body = (await authed_client.get(f"/queries/{query.id}")).json()
    assert status_body["column_schema"] is None


async def test_get_query_reports_column_schema_before_fetching_rows(
    authed_client: AsyncClient, workspace: Workspace, agent: Agent, db_session
):
    """A client learns the result types from the status response, without paging."""
    schema = [{"name": "en", "type": "ENUM('e', 'f')"}]
    query = await _done_query_with_agent(
        db_session, workspace, agent, row_count=1, result_schema=schema
    )

    resp = await authed_client.get(f"/queries/{query.id}")
    assert resp.status_code == 200
    assert resp.json()["column_schema"] == schema


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


async def test_list_saved_queries_includes_creator_name(
    authed_client: AsyncClient, workspace: Workspace
):
    await authed_client.post(
        f"/workspaces/{workspace.slug}/saved-queries",
        json={"name": "Mine", "sql": "SELECT 1"},
    )
    resp = await authed_client.get(f"/workspaces/{workspace.slug}/saved-queries")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["created_by_name"] == "Querier"


async def test_create_saved_query_overwrites_by_name(
    authed_client: AsyncClient, workspace: Workspace
):
    """Saving over an existing name updates the row instead of duplicating."""
    first = await authed_client.post(
        f"/workspaces/{workspace.slug}/saved-queries",
        json={"name": "Report", "sql": "SELECT 1"},
    )
    assert first.status_code == 201

    second = await authed_client.post(
        f"/workspaces/{workspace.slug}/saved-queries",
        json={"name": "Report", "sql": "SELECT 2"},
    )
    assert second.status_code == 200
    assert second.json()["id"] == first.json()["id"]
    assert second.json()["sql"] == "SELECT 2"

    listed = await authed_client.get(f"/workspaces/{workspace.slug}/saved-queries")
    assert len(listed.json()) == 1


async def test_saved_query_rename_and_delete_lifecycle(
    authed_client: AsyncClient, workspace: Workspace
):
    created = await authed_client.post(
        f"/workspaces/{workspace.slug}/saved-queries",
        json={"name": "Original", "sql": "SELECT 1"},
    )
    sq_id = created.json()["id"]

    renamed = await authed_client.patch(
        f"/workspaces/{workspace.slug}/saved-queries/{sq_id}",
        json={"name": "Renamed"},
    )
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "Renamed"
    assert renamed.json()["sql"] == "SELECT 1"

    deleted = await authed_client.delete(f"/workspaces/{workspace.slug}/saved-queries/{sq_id}")
    assert deleted.status_code == 204

    listed = await authed_client.get(f"/workspaces/{workspace.slug}/saved-queries")
    assert listed.json() == []

    missing = await authed_client.patch(
        f"/workspaces/{workspace.slug}/saved-queries/{sq_id}",
        json={"name": "Nope"},
    )
    assert missing.status_code == 404


async def test_update_delete_saved_query_reader_rejected(
    authed_client: AsyncClient, workspace: Workspace, db_session
):
    created = await authed_client.post(
        f"/workspaces/{workspace.slug}/saved-queries",
        json={"name": "Shared", "sql": "SELECT 1"},
    )
    sq_id = created.json()["id"]

    reader = User(
        email="reader2@queries.local",
        password_hash=hash_password("pw"),
        name="Reader Two",
        role="user",
    )
    db_session.add(reader)
    await db_session.flush()
    db_session.add(WorkspaceMember(workspace_id=workspace.id, user_id=reader.id, role="reader"))
    await db_session.commit()

    await authed_client.post("/auth/logout")
    await authed_client.post(
        "/auth/login", json={"email": "reader2@queries.local", "password": "pw"}
    )

    patched = await authed_client.patch(
        f"/workspaces/{workspace.slug}/saved-queries/{sq_id}",
        json={"name": "Hijack"},
    )
    assert patched.status_code == 403

    deleted = await authed_client.delete(f"/workspaces/{workspace.slug}/saved-queries/{sq_id}")
    assert deleted.status_code == 403


async def test_run_saved_query_stamps_last_run_at(
    authed_client: AsyncClient, workspace: Workspace, connected_agent
):
    agent, _ = connected_agent
    created = await authed_client.post(
        f"/workspaces/{workspace.slug}/saved-queries",
        json={"name": "Runnable", "sql": "SELECT 1"},
    )
    sq_id = created.json()["id"]
    assert created.json()["last_run_at"] is None

    run = await authed_client.post(
        f"/workspaces/{workspace.slug}/queries",
        json={"sql": "SELECT 1", "agent_id": str(agent.id), "saved_query_id": sq_id},
    )
    assert run.status_code == 202

    listed = await authed_client.get(f"/workspaces/{workspace.slug}/saved-queries")
    stamped = next(q for q in listed.json() if q["id"] == sq_id)
    assert stamped["last_run_at"] is not None


async def test_run_with_unknown_saved_query_id_still_runs(
    authed_client: AsyncClient, workspace: Workspace, connected_agent
):
    agent, _ = connected_agent
    run = await authed_client.post(
        f"/workspaces/{workspace.slug}/queries",
        json={
            "sql": "SELECT 1",
            "agent_id": str(agent.id),
            "saved_query_id": str(uuid.uuid4()),
        },
    )
    assert run.status_code == 202


# --- sql metadata ---


async def test_sql_metadata_no_agent_returns_503(authed_client: AsyncClient, workspace: Workspace):
    resp = await authed_client.get(f"/workspaces/{workspace.slug}/sql-metadata")
    assert resp.status_code == 503


# --- elastic pool target (agent_id omitted) ---


@pytest_asyncio.fixture
def elastic_enabled(monkeypatch):
    from api.config import settings
    from api.services.compute.backends import get_backend

    monkeypatch.setattr(settings, "elastic_compute_enabled", True)
    monkeypatch.setattr(settings, "elastic_provider", "null")
    monkeypatch.setattr(settings, "elastic_max_agents_per_pool", 1)
    backend = get_backend("null")
    backend._instances.clear()
    yield
    backend._instances.clear()


async def test_elastic_pool_target_requires_flag(authed_client: AsyncClient, workspace: Workspace):
    """With elastic disabled, omitting agent_id is a 422, not a silent pool run."""
    resp = await authed_client.post(
        f"/workspaces/{workspace.slug}/queries", json={"sql": "SELECT 1"}
    )
    assert resp.status_code == 422
    assert resp.json()["error"] == "agent_required"


async def test_elastic_pool_parks_queued_and_provisions(
    authed_client: AsyncClient, workspace: Workspace, db_session, elastic_enabled
):
    """No compatible agent connected → run parked queued, one elastic agent provisioned."""
    import sqlalchemy as sa

    resp = await authed_client.post(
        f"/workspaces/{workspace.slug}/queries", json={"sql": "SELECT 1"}
    )
    assert resp.status_code == 202
    body = resp.json()
    assert body["status"] == "queued"
    assert body["agent_id"] is None
    assert body["origin"] == "elastic"

    provisioned = (
        (await db_session.execute(sa.select(Agent).where(Agent.provider.is_not(None))))
        .scalars()
        .all()
    )
    assert len(provisioned) == 1
    assert provisioned[0].lifecycle == "provisioning"
    assert provisioned[0].pool_key == "object_store"


async def test_elastic_pool_dispatches_to_connected_agent(
    authed_client: AsyncClient,
    workspace: Workspace,
    connected_agent,
    elastic_enabled,
):
    """A compatible agent is already up → dispatch to it instead of provisioning."""
    agent, mock_ws = connected_agent
    resp = await authed_client.post(
        f"/workspaces/{workspace.slug}/queries", json={"sql": "SELECT 1"}
    )
    assert resp.status_code == 202
    body = resp.json()
    assert body["agent_id"] == str(agent.id)
    assert body["origin"] == "elastic"
    assert len(mock_ws.sent) == 1


async def test_elastic_pool_provisions_when_the_picked_agent_has_gone(
    authed_client: AsyncClient, workspace: Workspace, db_session, elastic_enabled
):
    """Presence is read from Postgres with a TTL, so the agent the picker returns can
    have lost its socket already. Dispatch then fails, and for a pool run that is not an
    error -- it means supply has to be provisioned. Previously the ValueError escaped as
    a 500."""
    from datetime import UTC, datetime

    import sqlalchemy as sa

    # Fresh presence on the row, but no socket in the registry: exactly the state a
    # terminating agent, or one whose replica died, leaves behind.
    agent = Agent(
        name="ghost",
        status="healthy",
        capabilities={"extensions": ["httpfs"]},
        owner_id="api",
        owner_url="http://127.0.0.1:8000",
        last_ping_at=datetime.now(tz=UTC),
    )
    db_session.add(agent)
    await db_session.commit()

    resp = await authed_client.post(
        f"/workspaces/{workspace.slug}/queries", json={"sql": "SELECT 1"}
    )

    assert resp.status_code == 202
    body = resp.json()
    assert body["status"] == "queued"
    assert body["agent_id"] is None

    provisioned = (
        (await db_session.execute(sa.select(Agent).where(Agent.provider.is_not(None))))
        .scalars()
        .all()
    )
    assert len(provisioned) == 1


async def test_reading_results_advances_the_elastic_idle_clock(
    authed_client: AsyncClient, workspace: Workspace, agent: Agent, db_session, monkeypatch
):
    """Results live on the agent, not in the control plane, so an agent reaped while
    someone is still paging takes the Parquet with it. The idle clock previously only
    advanced on dispatch, so a user who ran a query and came back later to scroll found
    a terminated agent and a 503."""
    import os
    import tempfile
    from datetime import UTC, datetime, timedelta

    import duckdb
    import httpx

    from api.models.query import Query
    from api.models.user import Credential
    from api.services import query as query_service

    def _parquet_bytes() -> bytes:
        with tempfile.NamedTemporaryFile(suffix=".parquet", delete=False) as fh:
            path = fh.name
        try:
            duckdb.connect().execute(f"COPY (SELECT 1 AS a) TO '{path}' (FORMAT PARQUET)")
            with open(path, "rb") as f:
                return f.read()
        finally:
            os.unlink(path)

    stale = datetime.now(tz=UTC) - timedelta(hours=2)
    agent.result_host = "127.0.0.1"
    agent.result_port = 8001
    agent.provider = "null"
    agent.lifecycle = "running"
    agent.instance_id = "dh-agent-paging"
    agent.provisioned_at = stale
    agent.last_active_at = stale
    db_session.add(agent)
    db_session.add(
        Credential(user_id=None, agent_id=agent.id, kind="agent_session", token="tok-paging")
    )
    query = Query(
        workspace_id=workspace.id,
        agent_id=agent.id,
        sql="SELECT 1",
        status="done",
        row_count=1,
        result_path="/var/duckhaven-agent/results/x.parquet",
        started_at=stale,
    )
    db_session.add(query)
    await db_session.commit()

    async def fake_proxy(agent_arg, query_arg, *, row_offset=None, row_limit=None, token=None):
        return httpx.Response(200, content=_parquet_bytes())

    monkeypatch.setattr(query_service, "proxy_rows", fake_proxy)

    resp = await authed_client.get(f"/queries/{query.id}/rows")
    assert resp.status_code == 200

    await db_session.refresh(agent)
    # SQLite hands back naive timestamps; production normalises the same way.
    last_active = agent.last_active_at
    if last_active.tzinfo is None:
        last_active = last_active.replace(tzinfo=UTC)
    assert last_active > stale


async def test_elastic_pool_run_stamps_saved_query_last_run_at(
    authed_client: AsyncClient, workspace: Workspace, elastic_enabled
):
    """Running a saved query against the pool counts as running it.

    The explicit-agent path stamps last_run_at; the elastic path never did, so a
    saved query only ever run against the pool reported "never run" in the UI
    while its runs sat in History.
    """
    created = await authed_client.post(
        f"/workspaces/{workspace.slug}/saved-queries",
        json={"name": "Pooled", "sql": "SELECT 1"},
    )
    sq_id = created.json()["id"]
    assert created.json()["last_run_at"] is None

    run = await authed_client.post(
        f"/workspaces/{workspace.slug}/queries",
        json={"sql": "SELECT 1", "saved_query_id": sq_id},
    )
    assert run.status_code == 202
    assert run.json()["origin"] == "elastic"

    listed = await authed_client.get(f"/workspaces/{workspace.slug}/saved-queries")
    stamped = next(q for q in listed.json() if q["id"] == sq_id)
    assert stamped["last_run_at"] is not None, "a pool run left the saved query 'never run'"


# --- per-agent access on dispatch --------------------------------------------


@pytest_asyncio.fixture
async def restricted_agent(db_session, agent: Agent):
    agent.access_mode = "restricted"
    db_session.add(agent)
    await db_session.commit()
    await db_session.refresh(agent)
    return agent


async def test_dispatch_to_a_restricted_agent_is_hidden(
    authed_client: AsyncClient, workspace: Workspace, restricted_agent: Agent
):
    """404 rather than 403, and *before* the connectivity probe, so an ungranted
    caller learns nothing about the agent — not even that it exists."""
    resp = await authed_client.post(
        f"/workspaces/{workspace.slug}/queries",
        json={"sql": "SELECT 1", "agent_id": str(restricted_agent.id)},
    )
    assert resp.status_code == 404


async def test_dispatch_to_a_granted_restricted_agent_succeeds(
    authed_client: AsyncClient,
    workspace: Workspace,
    connected_agent,
    user: User,
    db_session,
):
    agent, _ws = connected_agent
    agent.access_mode = "restricted"
    db_session.add(agent)
    db_session.add(AgentGrant(agent_id=agent.id, user_id=user.id, tier="use"))
    await db_session.commit()

    resp = await authed_client.post(
        f"/workspaces/{workspace.slug}/queries",
        json={"sql": "SELECT 1", "agent_id": str(agent.id)},
    )
    assert resp.status_code == 202


async def test_an_open_agent_stays_dispatchable_without_any_grant(
    authed_client: AsyncClient, workspace: Workspace, connected_agent
):
    """The behaviour-preserving default: `open` is what every agent is unless an
    operator restricts it, and it dispatches exactly as it did before the ACL."""
    agent, _ws = connected_agent
    resp = await authed_client.post(
        f"/workspaces/{workspace.slug}/queries",
        json={"sql": "SELECT 1", "agent_id": str(agent.id)},
    )
    assert resp.status_code == 202


async def test_set_concurrency_needs_operate_not_use(
    authed_client: AsyncClient, workspace: Workspace, connected_agent
):
    """`use` dispatches queries but must not retune admission for everyone on the
    agent. The agent is open, so the caller has `use` and no more."""
    agent, _ws = connected_agent
    resp = await authed_client.post(
        f"/workspaces/{workspace.slug}/queries",
        json={"sql": "SET duckhaven_concurrency = 'single'", "agent_id": str(agent.id)},
    )
    assert resp.status_code == 403
    assert resp.json()["error"] == "agent_forbidden"


async def test_elastic_auto_pick_skips_agents_the_caller_cannot_use(
    authed_client: AsyncClient, workspace: Workspace, connected_agent, db_session, monkeypatch
):
    """Omitting `agent_id` must not be a way around a denial on a specific agent."""
    from api.config import settings

    monkeypatch.setattr(settings, "elastic_compute_enabled", True)
    agent, _ws = connected_agent
    agent.access_mode = "restricted"
    db_session.add(agent)
    await db_session.commit()

    resp = await authed_client.post(
        f"/workspaces/{workspace.slug}/queries", json={"sql": "SELECT 1"}
    )
    assert resp.status_code == 202
    # Parked unbound rather than dispatched to the agent the caller cannot use.
    assert resp.json()["agent_id"] is None
    assert resp.json()["status"] == "queued"


# --- targeted terminated agent (agent_id names an idle-terminated elastic agent) ---


@pytest_asyncio.fixture
async def terminated_elastic_agent(db_session):
    from datetime import UTC, datetime, timedelta

    from api.models.agent import Agent

    a = Agent(
        name="cold-target",
        status="unavailable",
        capabilities={"extensions": ["httpfs"]},
        provider="null",
        lifecycle="terminated",
        pool_key="object_store",
        instance_id="dh-agent-target",
        provisioned_at=datetime.now(tz=UTC) - timedelta(seconds=3600),
        terminated_at=datetime.now(tz=UTC) - timedelta(seconds=60),
    )
    db_session.add(a)
    await db_session.commit()
    await db_session.refresh(a)
    return a


async def test_targeted_terminated_agent_parks_queued_and_restarts(
    authed_client: AsyncClient, workspace, db_session, terminated_elastic_agent, elastic_enabled
):
    """G3: a run naming an idle-terminated elastic agent starts it and waits, the
    way every other data platform treats a suspended warehouse — rather than 503ing
    and leaving that agent permanently unusable for interactive work."""
    import sqlalchemy as sa

    from api.models.query import Query

    old_instance = terminated_elastic_agent.instance_id
    resp = await authed_client.post(
        f"/workspaces/{workspace.slug}/queries",
        json={"sql": "SELECT 1", "agent_id": str(terminated_elastic_agent.id)},
    )
    assert resp.status_code == 202
    body = resp.json()
    assert body["status"] == "queued"
    # The client polls this exactly as it polls any other queued run.
    assert body["agent_id"] is None

    query = (await db_session.execute(sa.select(Query))).scalars().one()
    assert query.requested_agent_id == terminated_elastic_agent.id
    # Interactive: History must not report it as a pool or scheduled run.
    assert query.origin is None

    await db_session.refresh(terminated_elastic_agent)
    assert terminated_elastic_agent.lifecycle == "provisioning"
    assert terminated_elastic_agent.instance_id != old_instance


async def test_targeted_terminated_agent_records_catalog_and_timeout(
    authed_client: AsyncClient, workspace, db_session, terminated_elastic_agent, elastic_enabled
):
    """The dispatch happens outside this request, so what the caller chose has to
    be on the row or the replay silently falls back to the workspace defaults."""
    import sqlalchemy as sa

    from api.models.query import Query

    resp = await authed_client.post(
        f"/workspaces/{workspace.slug}/queries",
        json={
            "sql": "SELECT 1",
            "agent_id": str(terminated_elastic_agent.id),
            "timeout_s": 42.0,
            "catalog": "test_ws",
        },
    )
    assert resp.status_code == 202

    query = (await db_session.execute(sa.select(Query))).scalars().one()
    assert query.timeout_s == 42.0
    assert query.active_catalog == "test_ws"


async def test_targeted_offline_static_agent_still_503s(
    authed_client: AsyncClient, workspace, agent, elastic_enabled
):
    """A static agent has nothing to start; the restart branch must stay narrow."""
    resp = await authed_client.post(
        f"/workspaces/{workspace.slug}/queries",
        json={"sql": "SELECT 1", "agent_id": str(agent.id)},
    )
    assert resp.status_code == 503


# --------------------------------------------------------------------------
# History: paging, filtering, sorting
# --------------------------------------------------------------------------


@pytest_asyncio.fixture
async def history(db_session, workspace: Workspace, agent: Agent, user: User):
    """A workspace of runs shaped to exercise every History filter at once.

    Three of the twelve share a `started_at`, which is what a session
    dispatching a burst of statements actually looks like and is the case a
    cursor without a tiebreaker gets wrong.
    """
    from datetime import UTC, datetime, timedelta

    from api.models.query import Query

    base = datetime(2026, 8, 20, 12, 0, 0, tzinfo=UTC)
    rows: list[Query] = []

    def add(sql, *, minutes, status="done", duration_ms=0, finished=True, **kw):
        started = base + timedelta(minutes=minutes)
        rows.append(
            Query(
                workspace_id=workspace.id,
                agent_id=agent.id,
                user_id=user.id,
                sql=sql,
                status=status,
                started_at=started,
                finished_at=(started + timedelta(milliseconds=duration_ms)) if finished else None,
                duration_ms=duration_ms if finished else None,
                **kw,
            )
        )

    # Three runs sharing one timestamp.
    add("SELECT 'burst_a'", minutes=0, duration_ms=10)
    add("SELECT 'burst_b'", minutes=0, duration_ms=20)
    add("SELECT 'burst_c'", minutes=0, duration_ms=30)
    add("SELECT 'plain'", minutes=1, duration_ms=40)
    add("SELECT 100 * 2 AS pct", minutes=2, duration_ms=50)
    add("SELECT user_name FROM t", minutes=3, duration_ms=60)
    add("INSERT INTO t VALUES (1)", minutes=4, duration_ms=70)
    add("CREATE TABLE made (a int)", minutes=5, duration_ms=80)
    add("SELECT 'slow'", minutes=6, duration_ms=45_000)
    add("SELECT 'cancelled'", minutes=7, status="cancelled", duration_ms=15)
    # Failed with no agent-reported duration, but two minutes of wall clock.
    # This is the row a naive `duration_ms > N` filter would wrongly drop.
    started = base + timedelta(minutes=8)
    rows.append(
        Query(
            workspace_id=workspace.id,
            agent_id=agent.id,
            user_id=user.id,
            sql="SELECT 'hung_then_failed'",
            status="failed",
            started_at=started,
            finished_at=started + timedelta(minutes=2),
            duration_ms=None,
        )
    )
    # Failed fast, also with no reported duration.
    started = base + timedelta(minutes=9)
    rows.append(
        Query(
            workspace_id=workspace.id,
            agent_id=agent.id,
            user_id=user.id,
            sql="SELECT 'failed_fast'",
            status="failed",
            started_at=started,
            finished_at=started + timedelta(milliseconds=5),
            duration_ms=None,
        )
    )
    # Still running: duration unknown, not zero.
    add("SELECT 'still_running'", minutes=10, status="running", finished=False)
    # A row written before statement_type existed.
    add("SELECT 'unclassified'", minutes=11, duration_ms=5, statement_type=None)

    db_session.add_all(rows)
    await db_session.commit()
    # The listener classifies on insert; blank one out to stand for a legacy row.
    from sqlalchemy import update

    await db_session.execute(
        update(Query).where(Query.sql == "SELECT 'unclassified'").values(statement_type=None)
    )
    await db_session.commit()
    return rows


async def _sqls(client, workspace, **params):
    resp = await client.get(f"/workspaces/{workspace.slug}/queries", params=params)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    return [r["sql"] for r in body["items"]], body


async def test_history_returns_a_page_envelope(authed_client, workspace, history):
    sqls, body = await _sqls(authed_client, workspace)
    assert set(body) == {"items", "cursor", "has_more"}
    assert body["has_more"] is False
    assert body["cursor"] is None
    assert len(sqls) == len(history)


async def test_history_pages_through_every_row_exactly_once(authed_client, workspace, history):
    """The whole point: reaching past the first page, with nothing lost or doubled."""
    seen: list[str] = []
    cursor = None
    for _ in range(20):
        params = {"limit": 3}
        if cursor:
            params["cursor"] = cursor
        page, body = await _sqls(authed_client, workspace, **params)
        assert len(page) <= 3
        seen += page
        if not body["has_more"]:
            assert body["cursor"] is None
            break
        cursor = body["cursor"]
    else:
        raise AssertionError("paging did not terminate")

    assert len(seen) == len(history)
    assert len(set(seen)) == len(history), "a row was returned on two pages"
    unpaged, _ = await _sqls(authed_client, workspace)
    assert seen == unpaged, "paged order diverged from unpaged order"


async def test_history_paging_is_stable_when_rows_share_a_timestamp(
    authed_client, workspace, history
):
    """Without the id tiebreaker these three could reorder between pages."""
    first, _ = await _sqls(authed_client, workspace, limit=3)
    again, _ = await _sqls(authed_client, workspace, limit=3)
    assert first == again


async def test_history_paging_does_not_repeat_rows_when_a_run_is_inserted_midway(
    authed_client, workspace, agent, user, db_session, history
):
    """The reason this is keyset paging and not offset paging.

    Under OFFSET, a row inserted at the head between two requests shifts every
    later page down by one and the reader sees a row twice.
    """
    from datetime import UTC, datetime

    from api.models.query import Query

    page1, body = await _sqls(authed_client, workspace, limit=4)
    assert body["has_more"]

    db_session.add(
        Query(
            workspace_id=workspace.id,
            agent_id=agent.id,
            user_id=user.id,
            sql="SELECT 'arrived_mid_page'",
            status="done",
            started_at=datetime.now(UTC),
        )
    )
    await db_session.commit()

    page2, _ = await _sqls(authed_client, workspace, limit=4, cursor=body["cursor"])
    assert not set(page1) & set(page2)


async def test_history_rejects_a_malformed_cursor(authed_client, workspace, history):
    """Told, rather than silently handed page one and appearing to loop."""
    resp = await authed_client.get(
        f"/workspaces/{workspace.slug}/queries", params={"cursor": "not-a-cursor"}
    )
    assert resp.status_code == 422


async def test_history_rejects_a_cursor_from_a_different_sort(authed_client, workspace, history):
    _, body = await _sqls(authed_client, workspace, limit=2, sort="started_at")
    resp = await authed_client.get(
        f"/workspaces/{workspace.slug}/queries",
        params={"cursor": body["cursor"], "sort": "duration"},
    )
    assert resp.status_code == 422


async def test_history_searches_statement_text_case_insensitively(
    authed_client, workspace, history
):
    sqls, _ = await _sqls(authed_client, workspace, q="burst")
    assert sorted(sqls) == ["SELECT 'burst_a'", "SELECT 'burst_b'", "SELECT 'burst_c'"]
    upper, _ = await _sqls(authed_client, workspace, q="BURST")
    assert sorted(upper) == sorted(sqls)


async def test_history_search_treats_like_metacharacters_literally(
    authed_client, workspace, history
):
    """`%` and `_` are characters in SQL, not wildcards the user gets to inject."""
    # `%` would otherwise match everything.
    pct, _ = await _sqls(authed_client, workspace, q="100 % 2")
    assert pct == []
    pct, _ = await _sqls(authed_client, workspace, q="100 * 2")
    assert pct == ["SELECT 100 * 2 AS pct"]

    # `_` would otherwise match any single character, so this would also hit
    # "user_name" spelled with any separator.
    under, _ = await _sqls(authed_client, workspace, q="user_name")
    assert under == ["SELECT user_name FROM t"]
    # An underscore left unescaped would make both of these match that row.
    wildcarded, _ = await _sqls(authed_client, workspace, q="userxname")
    assert wildcarded == []
    nomatch, _ = await _sqls(authed_client, workspace, q="user%name")
    assert nomatch == []

    # A lone backslash must not break the escaping of what follows it.
    resp = await authed_client.get(f"/workspaces/{workspace.slug}/queries", params={"q": "\\"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["items"] == []


async def test_history_finds_a_run_by_full_id_and_by_prefix(authed_client, workspace, history):
    target = history[4]
    full, _ = await _sqls(authed_client, workspace, query_id=str(target.id))
    assert full == [target.sql]

    # The UI truncates ids to eight characters through `shortId`, so that is
    # what people paste.
    prefix, _ = await _sqls(authed_client, workspace, query_id=str(target.id)[:8])
    assert prefix == [target.sql]


async def test_history_id_lookup_cannot_reach_another_workspace(
    client, db_session, user, agent, workspace, history
):
    """Knowing an id must not be a way around the workspace scoping."""
    from api.models.query import Query

    other_ws, _ = await seed_workspace(
        db_session, user_id=user.id, slug="id-other-ws", name="Other", role=None
    )
    foreign = Query(
        workspace_id=other_ws.id, agent_id=agent.id, sql="SELECT 'foreign'", status="done"
    )
    db_session.add(foreign)
    await db_session.commit()

    await client.post("/auth/login", json={"email": "q@queries.local", "password": "pw"})
    resp = await client.get(
        f"/workspaces/{workspace.slug}/queries", params={"query_id": str(foreign.id)}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["items"] == []


async def test_history_rejects_an_id_that_is_not_a_uuid_or_prefix(
    authed_client, workspace, history
):
    resp = await authed_client.get(
        f"/workspaces/{workspace.slug}/queries", params={"query_id": "'; DROP TABLE queries--"}
    )
    assert resp.status_code == 422


async def test_history_filters_by_status_and_accepts_several(authed_client, workspace, history):
    failed, _ = await _sqls(authed_client, workspace, status=["failed"])
    assert sorted(failed) == ["SELECT 'failed_fast'", "SELECT 'hung_then_failed'"]

    multi, _ = await _sqls(authed_client, workspace, status=["failed", "cancelled"])
    assert "SELECT 'cancelled'" in multi
    assert len(multi) == 3


async def test_history_rejects_an_unknown_status(authed_client, workspace, history):
    """An unknown filter value is an error, not a silently unfiltered page."""
    resp = await authed_client.get(
        f"/workspaces/{workspace.slug}/queries", params={"status": "exploded"}
    )
    assert resp.status_code == 422


async def test_history_slow_filter_uses_wall_clock_when_the_agent_reported_none(
    authed_client, workspace, history
):
    """The trap this filter exists to avoid.

    `duration_ms` is the agent's execution time and is null for a run that died
    before reporting one. Filtering on it alone drops every failure — precisely
    backwards, because a query that hung for two minutes and then failed is the
    thing someone hunting slow queries is looking for.
    """
    slow, _ = await _sqls(authed_client, workspace, slower_than_ms=30_000)
    assert sorted(slow) == ["SELECT 'hung_then_failed'", "SELECT 'slow'"]

    # ...and the failure that was genuinely quick stays out.
    assert "SELECT 'failed_fast'" not in slow
    # ...as does the run whose duration is not yet known.
    assert "SELECT 'still_running'" not in slow


async def test_history_slow_filter_excludes_runs_that_have_not_finished(
    authed_client, workspace, history
):
    """A running query's duration is unknown, not zero."""
    everything, _ = await _sqls(authed_client, workspace, slower_than_ms=0)
    assert "SELECT 'still_running'" not in everything


async def test_history_filters_by_statement_type(authed_client, workspace, history):
    inserts, _ = await _sqls(authed_client, workspace, statement_type=["insert"])
    assert inserts == ["INSERT INTO t VALUES (1)"]

    ddl, _ = await _sqls(authed_client, workspace, statement_type=["insert", "create"])
    assert sorted(ddl) == ["CREATE TABLE made (a int)", "INSERT INTO t VALUES (1)"]


async def test_history_unclassified_rows_survive_until_the_type_filter_is_used(
    authed_client, workspace, history
):
    """Null means "we do not know", so it must not be swept into any type.

    Rows written before the column existed have no classification. They stay
    visible while the filter is off, and claim no type once it is on.
    """
    everything, _ = await _sqls(authed_client, workspace)
    assert "SELECT 'unclassified'" in everything

    selects, _ = await _sqls(authed_client, workspace, statement_type=["select"])
    assert "SELECT 'unclassified'" not in selects


async def test_history_rejects_an_unknown_statement_type(authed_client, workspace, history):
    resp = await authed_client.get(
        f"/workspaces/{workspace.slug}/queries", params={"statement_type": "banana"}
    )
    assert resp.status_code == 422


async def test_history_sorts_by_duration_in_both_directions_with_unknowns_last(
    authed_client, workspace, history
):
    """Sorting happens server-side over the whole set, before the page is cut."""
    desc, _ = await _sqls(authed_client, workspace, sort="duration", dir="desc")
    assert desc[0] == "SELECT 'hung_then_failed'"  # 2 minutes of wall clock
    assert desc[1] == "SELECT 'slow'"  # 45s reported
    # Unknown duration sorts last whichever way the list runs, so a run that has
    # not finished never heads a "slowest first" list.
    assert desc[-1] == "SELECT 'still_running'"

    asc, _ = await _sqls(authed_client, workspace, sort="duration", dir="asc")
    assert asc[0] == "SELECT 'failed_fast'"
    assert asc[-1] == "SELECT 'still_running'"


async def test_history_sorting_applies_across_pages_not_within_one(
    authed_client, workspace, history
):
    """The slowest run must lead page one even though it is not the newest."""
    page, body = await _sqls(authed_client, workspace, sort="duration", dir="desc", limit=2)
    assert page == ["SELECT 'hung_then_failed'", "SELECT 'slow'"]
    assert body["has_more"]


async def test_history_pages_correctly_under_every_supported_sort(
    authed_client, workspace, history
):
    for sort in ("started_at", "duration"):
        for direction in ("asc", "desc"):
            unpaged, _ = await _sqls(authed_client, workspace, sort=sort, dir=direction)
            seen, cursor = [], None
            for _ in range(20):
                params = {"limit": 3, "sort": sort, "dir": direction}
                if cursor:
                    params["cursor"] = cursor
                page, body = await _sqls(authed_client, workspace, **params)
                seen += page
                if not body["has_more"]:
                    break
                cursor = body["cursor"]
            else:
                raise AssertionError(f"paging did not terminate for {sort}/{direction}")
            assert seen == unpaged, f"paged order wrong for sort={sort} dir={direction}"


async def test_history_sorts_by_started_at_ascending(authed_client, workspace, history):
    asc, _ = await _sqls(authed_client, workspace, sort="started_at", dir="asc")
    desc, _ = await _sqls(authed_client, workspace, sort="started_at", dir="desc")
    assert asc == list(reversed(desc))


async def test_history_excludes_maintenance_probe_rows(authed_client, workspace, agent, db_session):
    """The Lakehouse-health scanner's `SELECT 1` is machinery, not someone's work.

    It writes one row per scanned table per cycle with a null user_id, so it
    floods History the moment the scope widens past a single user's runs.
    """
    from api.models.query import Query

    db_session.add_all(
        [
            Query(
                workspace_id=workspace.id,
                agent_id=agent.id,
                user_id=None,
                sql="SELECT 1",
                status="done",
                origin="maintenance",
            ),
            # Session statements are dbt/dlt work and must keep showing up.
            Query(
                workspace_id=workspace.id,
                agent_id=agent.id,
                sql="SELECT 'dbt_ran_this'",
                status="done",
                origin="session",
            ),
        ]
    )
    await db_session.commit()

    sqls, _ = await _sqls(authed_client, workspace)
    assert "SELECT 1" not in sqls
    assert "SELECT 'dbt_ran_this'" in sqls


async def test_history_time_filters_are_open_to_a_plain_member(authed_client, workspace, history):
    """Narrowing your own workspace to a window shows nothing new.

    It was admin-gated only because it arrived alongside the cross-principal
    filters; a member could already reach these rows by scrolling.
    """
    sqls, _ = await _sqls(authed_client, workspace, since="2026-08-20T12:07:00Z")
    assert sqls
    assert "SELECT 'burst_a'" not in sqls

    bounded, _ = await _sqls(
        authed_client, workspace, since="2026-08-20T12:00:00Z", until="2026-08-20T12:01:00Z"
    )
    assert sorted(bounded) == [
        "SELECT 'burst_a'",
        "SELECT 'burst_b'",
        "SELECT 'burst_c'",
        "SELECT 'plain'",
    ]


async def test_history_time_filter_does_not_unlock_cross_workspace_reads(
    authed_client, workspace, history
):
    """Relaxing since/until must not have widened the gate beside it."""
    resp = await authed_client.get(
        f"/workspaces/{workspace.slug}/queries",
        params={"all_workspaces": "true", "since": "2026-08-20T12:00:00Z"},
    )
    assert resp.status_code == 403


async def test_history_stamps_statement_type_on_newly_created_runs(
    authed_client, workspace, agent, db_session
):
    """Classification happens as the row is written, for every creation path."""
    from sqlalchemy import select

    from api.models.query import Query

    db_session.add(
        Query(
            workspace_id=workspace.id,
            agent_id=agent.id,
            sql="CREATE TABLE fresh (a int)",
            status="done",
        )
    )
    await db_session.commit()

    row = (
        await db_session.execute(select(Query).where(Query.sql == "CREATE TABLE fresh (a int)"))
    ).scalar_one()
    assert row.statement_type == "create"
