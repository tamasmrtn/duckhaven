import uuid

import pytest
import pytest_asyncio
from conftest import seed_workspace
from httpx import AsyncClient

from api.config import settings
from api.models.agent import Agent
from api.models.query import Query
from api.models.sql_session import SqlSession
from api.models.user import User
from api.services.agent_registry import registry
from api.services.auth import hash_password
from api.services.sql_sessions import service as session_service


class MockWebSocket:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send_text(self, text: str) -> None:
        self.sent.append(text)


@pytest_asyncio.fixture
async def user(db_session):
    u = User(email="s@sessions.local", password_hash=hash_password("pw"), name="Sess", role="user")
    db_session.add(u)
    await db_session.commit()
    await db_session.refresh(u)
    return u


@pytest_asyncio.fixture
async def authed_client(client: AsyncClient, user: User):
    await client.post("/auth/login", json={"email": "s@sessions.local", "password": "pw"})
    return client


@pytest_asyncio.fixture
async def workspace(db_session, user: User):
    ws, _catalog = await seed_workspace(db_session, user_id=user.id)
    return ws


@pytest_asyncio.fixture
async def agent(db_session):
    a = Agent(name="sess-agent", status="healthy", capabilities={"extensions": ["httpfs"]})
    db_session.add(a)
    await db_session.commit()
    await db_session.refresh(a)
    return a


@pytest_asyncio.fixture
async def connected_agent(agent: Agent):
    registry.register(agent.id, MockWebSocket())  # type: ignore[arg-type]
    yield agent
    registry.unregister(agent.id)


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setattr(settings, "sql_sessions_enabled", True)


async def _open_session_row(db, workspace, agent, user, *, status="open") -> SqlSession:
    s = SqlSession(
        workspace_id=workspace.id,
        agent_id=agent.id,
        user_id=user.id,
        status=status,
        active_catalog="test_ws",
        staging_uri="/tmp/test/_staging/x/",
    )
    db.add(s)
    await db.commit()
    await db.refresh(s)
    return s


async def test_disabled_by_default_returns_404(authed_client, workspace):
    resp = await authed_client.post(f"/workspaces/{workspace.slug}/sql/sessions", json={})
    assert resp.status_code == 404


async def test_open_session_success(
    authed_client, workspace, connected_agent, enabled, monkeypatch
):
    async def fake_dispatch(db, session, catalogs):
        session.status = "open"
        await db.commit()
        return True

    monkeypatch.setattr(session_service, "dispatch_open_session", fake_dispatch)

    resp = await authed_client.post(
        f"/workspaces/{workspace.slug}/sql/sessions", json={"agent_id": str(connected_agent.id)}
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "open"
    assert body["staging_uri"].endswith("/")
    assert body["agent_id"] == str(connected_agent.id)


async def test_open_session_timeout_marks_failed_and_dispatches_close(
    authed_client, db_session, workspace, connected_agent, enabled, monkeypatch
):
    # Dispatch succeeds but the agent never acks: the open must time out (504), CAS
    # the row to failed/open_timeout, and dispatch a close to reclaim any held slot.
    async def fake_open(db, session, catalogs):
        return True

    closed: list = []

    async def fake_close(db, agent_id, session_id):
        closed.append((agent_id, session_id))
        return True

    monkeypatch.setattr(session_service, "dispatch_open_session", fake_open)
    monkeypatch.setattr(session_service, "dispatch_close_session", fake_close)
    monkeypatch.setattr(settings, "sql_session_open_timeout_s", 0.05)

    resp = await authed_client.post(
        f"/workspaces/{workspace.slug}/sql/sessions", json={"agent_id": str(connected_agent.id)}
    )
    assert resp.status_code == 504, resp.text
    assert len(closed) == 1
    sess = await db_session.get(SqlSession, closed[0][1])
    assert sess.status == "failed"
    assert sess.error == "open_timeout"


async def test_open_session_timeout_does_not_clobber_late_open(
    authed_client, db_session, workspace, connected_agent, enabled, monkeypatch
):
    # The agent wins the race: the row flips to open between our last poll and the
    # timeout CAS. The compare-and-set must not overwrite it — return the open row.
    import sqlalchemy as sa

    from api.services.sql_sessions import service as svc

    async def fake_await(db, session, timeout_s, poll_interval_s=0.1):
        # Flip the DB row open without touching the in-memory ORM object (still
        # "opening"), so the endpoint reaches the opening-timeout CAS branch.
        await db.execute(
            sa.update(SqlSession)
            .where(SqlSession.id == session.id)
            .values(status="open")
            .execution_options(synchronize_session=False)
        )
        await db.commit()
        return session

    closed: list = []

    async def fake_open(db, session, catalogs):
        return True

    async def fake_close(db, agent_id, session_id):
        closed.append((agent_id, session_id))
        return True

    monkeypatch.setattr(session_service, "dispatch_open_session", fake_open)
    monkeypatch.setattr(session_service, "dispatch_close_session", fake_close)
    monkeypatch.setattr(svc, "await_session_open", fake_await)

    resp = await authed_client.post(
        f"/workspaces/{workspace.slug}/sql/sessions", json={"agent_id": str(connected_agent.id)}
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["status"] == "open"
    # No spurious close was dispatched for a session the agent legitimately opened.
    assert closed == []


async def test_open_session_agent_not_connected(authed_client, workspace, agent, enabled):
    resp = await authed_client.post(
        f"/workspaces/{workspace.slug}/sql/sessions", json={"agent_id": str(agent.id)}
    )
    assert resp.status_code == 503


async def test_statement_policy_rejection(
    authed_client, db_session, workspace, agent, user, enabled
):
    session = await _open_session_row(db_session, workspace, agent, user)
    resp = await authed_client.post(
        f"/sql/sessions/{session.id}/statements", json={"sql": "INSTALL httpfs"}
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "statement_not_allowed"


async def test_truncate_statement_is_accepted(
    authed_client, db_session, workspace, agent, user, enabled, monkeypatch
):
    """dbt's seed reset emits `TRUNCATE TABLE`; the policy used to reject it as an
    unknown statement type even though the one-shot path already allowed it."""
    session = await _open_session_row(db_session, workspace, agent, user)

    async def fake_exec(db, sess, query, timeout_s):
        return True

    monkeypatch.setattr(session_service, "dispatch_exec_statement", fake_exec)

    resp = await authed_client.post(
        f"/sql/sessions/{session.id}/statements",
        json={"sql": "TRUNCATE TABLE analytics.seed_countries"},
    )
    assert resp.status_code == 202, resp.text


async def test_truncate_database_is_rejected(
    authed_client, db_session, workspace, agent, user, enabled
):
    session = await _open_session_row(db_session, workspace, agent, user)
    resp = await authed_client.post(
        f"/sql/sessions/{session.id}/statements", json={"sql": "TRUNCATE DATABASE d"}
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "statement_not_allowed"


async def test_statement_on_non_open_session_conflicts(
    authed_client, db_session, workspace, agent, user, enabled
):
    session = await _open_session_row(db_session, workspace, agent, user, status="opening")
    resp = await authed_client.post(
        f"/sql/sessions/{session.id}/statements", json={"sql": "SELECT 1"}
    )
    assert resp.status_code == 409


async def test_statement_success_creates_session_query(
    authed_client, db_session, workspace, agent, user, enabled, monkeypatch
):
    session = await _open_session_row(db_session, workspace, agent, user)

    async def fake_exec(db, sess, query, timeout_s):
        return True

    monkeypatch.setattr(session_service, "dispatch_exec_statement", fake_exec)

    resp = await authed_client.post(
        f"/sql/sessions/{session.id}/statements", json={"sql": "SELECT 1"}
    )
    assert resp.status_code == 202, resp.text
    query = await db_session.get(Query, uuid.UUID(resp.json()["id"]))
    assert query.origin == "session"
    assert query.session_id == session.id


async def test_statement_persists_its_timeout_budget(
    authed_client, db_session, workspace, agent, user, enabled, monkeypatch
):
    """The budget used to travel only on the wire, so nothing server-side could
    bound a statement whose dispatch frame was lost (#156). The reaper reads it
    off the row."""
    session = await _open_session_row(db_session, workspace, agent, user)

    async def fake_exec(db, sess, query, timeout_s):
        return True

    monkeypatch.setattr(session_service, "dispatch_exec_statement", fake_exec)

    resp = await authed_client.post(
        f"/sql/sessions/{session.id}/statements", json={"sql": "SELECT 1", "timeout_s": 42.0}
    )
    assert resp.status_code == 202, resp.text
    query = await db_session.get(Query, uuid.UUID(resp.json()["id"]))
    assert query.timeout_s == 42.0


async def test_statement_persists_the_default_timeout_budget(
    authed_client, db_session, workspace, agent, user, enabled, monkeypatch
):
    session = await _open_session_row(db_session, workspace, agent, user)

    async def fake_exec(db, sess, query, timeout_s):
        return True

    monkeypatch.setattr(session_service, "dispatch_exec_statement", fake_exec)

    resp = await authed_client.post(
        f"/sql/sessions/{session.id}/statements", json={"sql": "SELECT 1"}
    )
    query = await db_session.get(Query, uuid.UUID(resp.json()["id"]))
    assert query.timeout_s == 600.0


async def test_statement_dispatch_failure_marks_the_row_failed(
    authed_client, db_session, workspace, agent, user, enabled, monkeypatch
):
    """Previously a dispatch failure left the just-committed `queued` row to be
    silently abandoned rather than resolved (#156's defect 1). It must come back
    503 *and* leave the row in a terminal state, not a dangling queued one."""
    session = await _open_session_row(db_session, workspace, agent, user)

    async def fake_exec(db, sess, query, timeout_s):
        return False

    monkeypatch.setattr(session_service, "dispatch_exec_statement", fake_exec)

    resp = await authed_client.post(
        f"/sql/sessions/{session.id}/statements", json={"sql": "SELECT 1"}
    )
    assert resp.status_code == 503

    result = await db_session.execute(
        Query.__table__.select().where(Query.session_id == session.id)
    )
    row = result.mappings().one()
    assert row["status"] == "failed"
    assert row["error"] == "agent not connected"


async def test_close_session(
    authed_client, db_session, workspace, agent, user, enabled, monkeypatch
):
    session = await _open_session_row(db_session, workspace, agent, user)

    async def fake_close(db, agent_id, session_id):
        return True

    monkeypatch.setattr(session_service, "dispatch_close_session", fake_close)

    resp = await authed_client.delete(f"/sql/sessions/{session.id}")
    assert resp.status_code == 204
    await db_session.refresh(session)
    assert session.status == "closing"


# ── Staging files (presigned URLs, issue #160) ────────────────────────────────


def _patch_presign(monkeypatch):
    """Patch the presign service so the router test needs no real MinIO/boto3."""
    from datetime import UTC, datetime

    from api.routers import sql_sessions
    from api.services.staging_presign import StagedFile

    expires = datetime(2026, 7, 18, tzinfo=UTC)

    def fake_presign(catalog, session_id, names, *, ttl_s):
        files = [
            StagedFile(
                name=n,
                key=f"s3://warehouse/_staging/{session_id}/{n}",
                put_url=f"http://localhost:9000/warehouse/_staging/{session_id}/{n}?put",
                get_url=f"http://minio:9000/warehouse/_staging/{session_id}/{n}?get",
            )
            for n in names
        ]
        return files, expires

    monkeypatch.setattr(sql_sessions.staging_presign, "presign_staging_files", fake_presign)
    return expires


async def test_staging_files_disabled_returns_404(
    authed_client, db_session, workspace, agent, user
):
    session = await _open_session_row(db_session, workspace, agent, user)
    resp = await authed_client.post(
        f"/sql/sessions/{session.id}/staging-files", json={"files": ["o.parquet"]}
    )
    assert resp.status_code == 404


async def test_staging_files_success(
    authed_client, db_session, workspace, agent, user, enabled, monkeypatch
):
    _patch_presign(monkeypatch)
    session = await _open_session_row(db_session, workspace, agent, user)

    resp = await authed_client.post(
        f"/sql/sessions/{session.id}/staging-files",
        json={"files": ["orders.parquet", "items.parquet"]},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert [f["name"] for f in body["files"]] == ["orders.parquet", "items.parquet"]
    first = body["files"][0]
    assert first["put_url"].startswith("http://localhost:9000/")
    assert first["get_url"].startswith("http://minio:9000/")
    assert str(session.id) in first["key"]
    assert body["expires_at"].startswith("2026-07-18")


async def test_staging_files_non_open_conflicts(
    authed_client, db_session, workspace, agent, user, enabled, monkeypatch
):
    _patch_presign(monkeypatch)
    session = await _open_session_row(db_session, workspace, agent, user, status="closed")
    resp = await authed_client.post(
        f"/sql/sessions/{session.id}/staging-files", json={"files": ["o.parquet"]}
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "session_not_open"


async def test_staging_files_rejects_path_traversal(
    authed_client, db_session, workspace, agent, user, enabled
):
    session = await _open_session_row(db_session, workspace, agent, user)
    for bad in (["a/b.parquet"], ["../escape"], [""], []):
        resp = await authed_client.post(
            f"/sql/sessions/{session.id}/staging-files", json={"files": bad}
        )
        assert resp.status_code == 422, bad


async def test_staging_files_unavailable_backend_422(
    authed_client, db_session, workspace, agent, user, enabled, monkeypatch
):
    from api.routers import sql_sessions
    from api.services.staging_presign import StagingUnavailable

    def boom(catalog, session_id, names, *, ttl_s):
        raise StagingUnavailable("no staging location")

    monkeypatch.setattr(sql_sessions.staging_presign, "presign_staging_files", boom)
    session = await _open_session_row(db_session, workspace, agent, user)
    resp = await authed_client.post(
        f"/sql/sessions/{session.id}/staging-files", json={"files": ["o.parquet"]}
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "staging_unavailable"


async def test_read_parquet_of_own_staging_get_url_is_admitted(
    authed_client, db_session, workspace, agent, user, enabled, monkeypatch
):
    from api.config import settings as cfg

    session = await _open_session_row(db_session, workspace, agent, user)

    async def fake_exec(db, sess, query, timeout_s):
        return True

    monkeypatch.setattr(session_service, "dispatch_exec_statement", fake_exec)

    # A get_url under this session's own staging prefix (object_store catalog with
    # root_uri="/tmp/test" -> internal-endpoint https prefix) must be admitted.
    url = (
        f"{cfg.s3_endpoint_internal}/{cfg.s3_bucket}/tmp/test/_staging/"
        f"{session.id}/o.parquet?X-Amz-Signature=abc"
    )
    resp = await authed_client.post(
        f"/sql/sessions/{session.id}/statements",
        json={"sql": f"SELECT * FROM read_parquet('{url}')"},
    )
    assert resp.status_code == 202, resp.text


async def test_read_parquet_of_foreign_url_is_rejected(
    authed_client, db_session, workspace, agent, user, enabled
):
    session = await _open_session_row(db_session, workspace, agent, user)
    resp = await authed_client.post(
        f"/sql/sessions/{session.id}/statements",
        json={"sql": "SELECT * FROM read_parquet('https://evil.example.com/secret.parquet')"},
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "statement_not_allowed"
