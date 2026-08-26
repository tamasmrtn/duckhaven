import uuid
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from conftest import seed_workspace
from httpx import AsyncClient

from api.config import settings
from api.models.agent import Agent
from api.models.agent_grant import AgentGrant
from api.models.query import Query
from api.models.sql_session import SqlSession
from api.models.user import User
from api.services.agent_registry import registry
from api.services.auth import hash_password
from api.services.sql_sessions import service as session_service
from api.services.sql_sessions.client_info import parse_user_agent
from duckhaven_shared.protocol import Frame, FrameType


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
    assert resp.json()["error"] == "statement_not_allowed"


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


@pytest.mark.parametrize(
    "sql",
    [
        "DESCRIBE analytics.events",
        # The subquery form dlt and dbt emit for column metadata.
        "SELECT column_name, column_type FROM (DESCRIBE analytics.events)",
        "SHOW TABLES",
        "SHOW ALL TABLES",
        "SUMMARIZE analytics.events",
        "PRAGMA table_info('analytics.events')",
        "PRAGMA database_list",
        "PRAGMA version",
    ],
)
async def test_introspection_statements_are_accepted(
    authed_client, db_session, workspace, agent, user, enabled, monkeypatch, sql
):
    """Relation introspection is how every client discovers schemas and columns.

    These already passed the one-shot `/queries` allowlist and the agent already
    materializes them, but the session policy rejected them as unknown statement
    types — so dbt and dlt, which live on the session path, could not introspect
    at all. `information_schema.columns` cannot substitute: it does not work for
    attached Iceberg relations (see docs/reference/sql-support.md).
    """
    session = await _open_session_row(db_session, workspace, agent, user)

    async def fake_exec(db, sess, query, timeout_s):
        return True

    monkeypatch.setattr(session_service, "dispatch_exec_statement", fake_exec)

    resp = await authed_client.post(f"/sql/sessions/{session.id}/statements", json={"sql": sql})
    assert resp.status_code == 202, resp.text


@pytest.mark.parametrize(
    "sql",
    [
        # PRAGMA is also DuckDB's spelling of SET; the sandbox-widening ones must
        # stay rejected even though the row-returning ones are now admitted.
        "PRAGMA memory_limit = '8GB'",
        "PRAGMA enable_external_access = true",
        "PRAGMA disable_verification",
        # Only the argument-less SHOW forms are admitted.
        "SHOW DATABASES",
    ],
)
async def test_sandbox_widening_pragmas_stay_rejected(
    authed_client, db_session, workspace, agent, user, enabled, sql
):
    session = await _open_session_row(db_session, workspace, agent, user)
    resp = await authed_client.post(f"/sql/sessions/{session.id}/statements", json={"sql": sql})
    assert resp.status_code == 422
    assert resp.json()["error"] == "statement_not_allowed"


async def test_truncate_database_is_rejected(
    authed_client, db_session, workspace, agent, user, enabled
):
    session = await _open_session_row(db_session, workspace, agent, user)
    resp = await authed_client.post(
        f"/sql/sessions/{session.id}/statements", json={"sql": "TRUNCATE DATABASE d"}
    )
    assert resp.status_code == 422
    assert resp.json()["error"] == "statement_not_allowed"


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
    assert resp.json()["error"] == "session_not_open"


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
    assert resp.json()["error"] == "staging_unavailable"


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
    assert resp.json()["error"] == "statement_not_allowed"


# ── Audit surface: close reason, client identity, list, statement timeline ────


def _statement(session: SqlSession, sql: str, *, status: str = "done") -> Query:
    return Query(
        workspace_id=session.workspace_id,
        agent_id=session.agent_id,
        user_id=session.user_id,
        sql=sql,
        status=status,
        origin="session",
        session_id=session.id,
    )


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("dbt-duckhaven/1.2.0", ("dbt-duckhaven", "1.2.0")),
        ("dlt-duckhaven/0.4.1 (linux; cpython 3.12)", ("dlt-duckhaven", "0.4.1")),
        # Client leads with the calling application (connector >= 0.3.0): the
        # workload is recorded, the trailing connector token is dropped.
        (
            "dbt-duckhaven/0.1.0 duckhaven-sql-connector/0.3.0",
            ("dbt-duckhaven", "0.1.0"),
        ),
        # Legacy/misordered order (connector < 0.3.0): the connector's own leading
        # token wins and the workload is lost — the shape of issue #179.
        (
            "duckhaven-sql-connector/0.2.0 dbt-duckhaven/0.1.0",
            ("duckhaven-sql-connector", "0.2.0"),
        ),
        ("curl", ("curl", None)),
        ("", (None, None)),
        (None, (None, None)),
        ("   ", (None, None)),
        ("/1.0", (None, None)),
    ],
)
def test_parse_user_agent(header, expected):
    assert parse_user_agent(header) == expected


def test_parse_user_agent_truncates_absurd_values():
    name, version = parse_user_agent("x" * 500 + "/" + "9" * 500)
    assert len(name) == 64
    assert len(version) == 32


async def test_open_session_records_the_client(
    authed_client, db_session, workspace, connected_agent, enabled, monkeypatch
):
    async def fake_dispatch(db, session, catalogs):
        session.status = "open"
        await db.commit()
        return True

    monkeypatch.setattr(session_service, "dispatch_open_session", fake_dispatch)

    resp = await authed_client.post(
        f"/workspaces/{workspace.slug}/sql/sessions",
        json={"agent_id": str(connected_agent.id)},
        headers={"User-Agent": "dbt-duckhaven/1.2.0"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["client_name"] == "dbt-duckhaven"
    assert body["client_version"] == "1.2.0"
    assert body["close_reason"] is None


async def test_explicit_close_records_the_client_reason(
    authed_client, db_session, workspace, agent, user, enabled, monkeypatch
):
    session = await _open_session_row(db_session, workspace, agent, user)

    async def fake_close(db, agent_id, session_id):
        return True

    monkeypatch.setattr(session_service, "dispatch_close_session", fake_close)
    session_id = session.id
    await authed_client.delete(f"/sql/sessions/{session_id}")
    # The request ran on its own DB session; drop this one's cached copy so the
    # frame handler below sees the committed `closing` status.
    db_session.expire_all()

    # The reason lands when the agent acks the close, not when it is requested.
    await session_service.handle_session_frame(
        db_session,
        Frame(type=FrameType.SESSION_CLOSED, payload={"session_id": str(session_id)}),
    )
    reloaded = await db_session.get(SqlSession, session_id)
    assert reloaded.status == "closed"
    assert reloaded.close_reason == "client"


async def test_agent_disconnect_records_its_reason(db_session, workspace, agent, user):
    session = await _open_session_row(db_session, workspace, agent, user)
    await session_service.fail_sessions_for_agent(db_session, agent.id)
    await db_session.refresh(session)
    assert session.status == "failed"
    assert session.close_reason == "agent_disconnect"


# The idle / max_lifetime / open_timeout reasons are covered where the reaper
# lives: tests/unit/services/sql_sessions/test_reaper.py.


# ── Session list ──────────────────────────────────────────────────────────────


async def test_list_sessions_is_newest_first_with_counts_and_names(
    authed_client, db_session, workspace, agent, user, enabled
):
    older = await _open_session_row(db_session, workspace, agent, user, status="closed")
    newer = await _open_session_row(db_session, workspace, agent, user)
    older.created_at = datetime.now(tz=UTC) - timedelta(hours=1)
    db_session.add_all([_statement(newer, "SELECT 1"), _statement(newer, "SELECT 2")])
    await db_session.commit()

    resp = await authed_client.get(f"/workspaces/{workspace.slug}/sql/sessions")
    assert resp.status_code == 200, resp.text
    rows = resp.json()
    assert [r["id"] for r in rows] == [str(newer.id), str(older.id)]
    assert rows[0]["statement_count"] == 2
    assert rows[1]["statement_count"] == 0
    assert rows[0]["user_name"] == "Sess"
    assert rows[0]["agent_name"] == "sess-agent"


async def test_list_sessions_filters_by_status(
    authed_client, db_session, workspace, agent, user, enabled
):
    await _open_session_row(db_session, workspace, agent, user)
    closed = await _open_session_row(db_session, workspace, agent, user, status="closed")

    resp = await authed_client.get(
        f"/workspaces/{workspace.slug}/sql/sessions", params={"status": "closed"}
    )
    assert [r["id"] for r in resp.json()] == [str(closed.id)]


async def test_list_sessions_disabled_returns_404(authed_client, workspace):
    resp = await authed_client.get(f"/workspaces/{workspace.slug}/sql/sessions")
    assert resp.status_code == 404


async def test_list_sessions_rejects_admin_filters_for_non_admin(
    authed_client, workspace, user, enabled
):
    resp = await authed_client.get(
        f"/workspaces/{workspace.slug}/sql/sessions", params={"user_id": str(user.id)}
    )
    assert resp.status_code == 403


async def test_non_member_cannot_list_sessions(client, db_session, workspace, enabled):
    outsider = User(
        email="out@sessions.local",
        password_hash=hash_password("pw"),
        name="Outsider",
        role="user",
    )
    db_session.add(outsider)
    await db_session.commit()
    await client.post("/auth/login", json={"email": "out@sessions.local", "password": "pw"})

    resp = await client.get(f"/workspaces/{workspace.slug}/sql/sessions")
    assert resp.status_code == 403


# ── Statement timeline ────────────────────────────────────────────────────────


async def test_statement_timeline_is_ordered_and_scoped(
    authed_client, db_session, workspace, agent, user, enabled
):
    session = await _open_session_row(db_session, workspace, agent, user)
    other = await _open_session_row(db_session, workspace, agent, user)
    first = _statement(session, "CREATE TABLE t (a INT)")
    second = _statement(session, "INSERT INTO t VALUES (1)")
    first.started_at = datetime.now(tz=UTC) - timedelta(minutes=5)
    db_session.add_all([second, first, _statement(other, "SELECT 99")])
    await db_session.commit()

    resp = await authed_client.get(f"/sql/sessions/{session.id}/statements")
    assert resp.status_code == 200, resp.text
    rows = resp.json()
    assert [r["sql"] for r in rows] == ["CREATE TABLE t (a INT)", "INSERT INTO t VALUES (1)"]
    assert all(r["session_id"] == str(session.id) for r in rows)


async def test_statement_timeline_disabled_returns_404(
    authed_client, db_session, workspace, agent, user
):
    session = await _open_session_row(db_session, workspace, agent, user)
    resp = await authed_client.get(f"/sql/sessions/{session.id}/statements")
    assert resp.status_code == 404


async def test_non_member_cannot_read_the_statement_timeline(
    client, db_session, workspace, agent, user, enabled
):
    session = await _open_session_row(db_session, workspace, agent, user)
    outsider = User(
        email="out2@sessions.local",
        password_hash=hash_password("pw"),
        name="Outsider",
        role="user",
    )
    db_session.add(outsider)
    await db_session.commit()
    await client.post("/auth/login", json={"email": "out2@sessions.local", "password": "pw"})

    resp = await client.get(f"/sql/sessions/{session.id}/statements")
    assert resp.status_code == 403


# --- per-agent access on session open ----------------------------------------


async def test_open_session_on_a_restricted_agent_is_hidden(
    authed_client: AsyncClient, workspace, agent: Agent, db_session, enabled
):
    """Checked ahead of the connectivity probe, so an ungranted caller cannot even
    tell whether the agent is up."""
    agent.access_mode = "restricted"
    db_session.add(agent)
    await db_session.commit()

    resp = await authed_client.post(
        f"/workspaces/{workspace.slug}/sql/sessions", json={"agent_id": str(agent.id)}
    )
    assert resp.status_code == 404


async def test_open_session_auto_pick_skips_unusable_agents(
    authed_client: AsyncClient, workspace, connected_agent: Agent, db_session, enabled
):
    """No `agent_id` must not become a way around a denial on the only agent."""
    connected_agent.access_mode = "restricted"
    db_session.add(connected_agent)
    await db_session.commit()

    resp = await authed_client.post(f"/workspaces/{workspace.slug}/sql/sessions", json={})
    assert resp.status_code == 503
    assert resp.json()["message"] == "No connected agent available"


async def test_open_session_auto_pick_finds_a_granted_agent(
    authed_client: AsyncClient, workspace, connected_agent: Agent, db_session, user: User, enabled
):
    connected_agent.access_mode = "restricted"
    db_session.add(connected_agent)
    db_session.add(AgentGrant(agent_id=connected_agent.id, user_id=user.id, tier="use"))
    await db_session.commit()

    resp = await authed_client.post(f"/workspaces/{workspace.slug}/sql/sessions", json={})
    # Reaches dispatch (the agent's ack never arrives in this test, so it times out
    # rather than 503-ing on selection).
    assert resp.status_code != 503


# ── Cold start: starting compute for a session ────────────────────────────────


@pytest_asyncio.fixture
def elastic_enabled(monkeypatch):
    from api.services.compute.backends import get_backend

    monkeypatch.setattr(settings, "elastic_compute_enabled", True)
    monkeypatch.setattr(settings, "elastic_provider", "null")
    monkeypatch.setattr(settings, "elastic_max_agents_per_pool", 1)
    backend = get_backend("null")
    backend._instances.clear()
    yield
    backend._instances.clear()


@pytest_asyncio.fixture
async def terminated_agent(db_session):
    a = Agent(
        name="cold-agent",
        status="unavailable",
        capabilities={"extensions": ["httpfs"]},
        provider="null",
        lifecycle="terminated",
        pool_key="object_store",
        instance_id="dh-agent-cold",
        provisioned_at=datetime.now(tz=UTC) - timedelta(seconds=3600),
        terminated_at=datetime.now(tz=UTC) - timedelta(seconds=60),
    )
    db_session.add(a)
    await db_session.commit()
    await db_session.refresh(a)
    return a


async def _agent_rows(db):
    import sqlalchemy as sa

    return (await db.execute(sa.select(Agent))).scalars().all()


async def test_open_with_no_agent_parks_pending_and_provisions(
    authed_client, db_session, workspace, enabled, elastic_enabled
):
    """G1: a cold pool starts compute instead of answering 503."""
    resp = await authed_client.post(
        f"/workspaces/{workspace.slug}/sql/sessions",
        json={"wait_timeout_s": 0, "on_wait_timeout": "continue"},
    )
    assert resp.status_code == 202
    body = resp.json()
    assert body["status"] == "pending"
    assert body["agent_id"] is None
    # Catalog and staging prefix are workspace properties, so a pending session is
    # fully described before any agent exists.
    assert body["active_catalog"] == "test_ws"
    assert body["staging_uri"]

    rows = [a for a in await _agent_rows(db_session) if a.provider is not None]
    assert len(rows) == 1
    assert rows[0].lifecycle == "provisioning"
    assert rows[0].pool_key == "object_store"


async def test_open_with_a_terminated_agent_restarts_it_and_parks(
    authed_client, db_session, workspace, terminated_agent, enabled, elastic_enabled
):
    """G2: naming an idle-terminated elastic agent starts it rather than 503ing.

    The reaper took this agent down *because* nothing was using it, so refusing
    here would make it permanently unusable for interactive work.
    """
    import sqlalchemy as sa

    old_instance = terminated_agent.instance_id
    resp = await authed_client.post(
        f"/workspaces/{workspace.slug}/sql/sessions",
        json={
            "agent_id": str(terminated_agent.id),
            "wait_timeout_s": 0,
            "on_wait_timeout": "continue",
        },
    )
    assert resp.status_code == 202
    assert resp.json()["status"] == "pending"

    session = (await db_session.execute(sa.select(SqlSession))).scalars().one()
    assert session.agent_id is None
    assert session.requested_agent_id == terminated_agent.id

    await db_session.refresh(terminated_agent)
    assert terminated_agent.lifecycle == "provisioning"
    assert terminated_agent.instance_id != old_instance


async def test_open_with_a_terminated_agent_needs_only_use_tier(
    authed_client, db_session, workspace, terminated_agent, enabled, elastic_enabled
):
    """Implicitly starting an agent by sending it work is dispatch, not a lifecycle
    operation — the tier the scheduler already restarts at. A restricted agent the
    caller has no grant on still 404s, exactly as a connected one would."""
    resp = await authed_client.post(
        f"/workspaces/{workspace.slug}/sql/sessions",
        json={
            "agent_id": str(terminated_agent.id),
            "wait_timeout_s": 0,
            "on_wait_timeout": "continue",
        },
    )
    assert resp.status_code == 202

    terminated_agent.access_mode = "restricted"
    terminated_agent.lifecycle = "terminated"
    await db_session.commit()

    resp = await authed_client.post(
        f"/workspaces/{workspace.slug}/sql/sessions",
        json={
            "agent_id": str(terminated_agent.id),
            "wait_timeout_s": 0,
            "on_wait_timeout": "continue",
        },
    )
    assert resp.status_code == 404


async def test_open_with_an_offline_static_agent_still_503s(
    authed_client, workspace, agent, enabled, elastic_enabled
):
    """A static agent has nothing to start; the restart branch must stay narrow."""
    resp = await authed_client.post(
        f"/workspaces/{workspace.slug}/sql/sessions", json={"agent_id": str(agent.id)}
    )
    assert resp.status_code == 503


async def test_open_with_a_disconnected_running_elastic_agent_still_503s(
    authed_client, db_session, workspace, terminated_agent, enabled, elastic_enabled
):
    """`running` but socketless is a connectivity problem, not a cold pool — the
    same rule the scheduler applies."""
    terminated_agent.lifecycle = "running"
    await db_session.commit()

    resp = await authed_client.post(
        f"/workspaces/{workspace.slug}/sql/sessions", json={"agent_id": str(terminated_agent.id)}
    )
    assert resp.status_code == 503
    assert resp.json()["message"] == "Agent not connected"


async def test_open_gives_up_with_503_and_retry_after_by_default(
    authed_client, db_session, workspace, enabled, elastic_enabled, monkeypatch
):
    """The default answer for a client that cannot poll. Crucially it abandons the
    session row, never the compute — an immediate retry should land on the agent
    that is still coming up rather than pay for a second cold start."""
    import sqlalchemy as sa

    monkeypatch.setattr(settings, "sql_session_wait_timeout_s", 0.3)

    resp = await authed_client.post(f"/workspaces/{workspace.slug}/sql/sessions", json={})
    assert resp.status_code == 503
    assert resp.json()["error"] == "compute_starting"
    assert resp.headers["retry-after"] == "5"

    session = (await db_session.execute(sa.select(SqlSession))).scalars().one()
    assert session.status == "failed"
    assert session.close_reason == "compute_timeout"

    provisioned = [a for a in await _agent_rows(db_session) if a.provider is not None]
    assert [a.lifecycle for a in provisioned] == ["provisioning"]


async def test_continue_returns_202_then_the_session_opens(
    authed_client, db_session, workspace, enabled, elastic_enabled, monkeypatch
):
    """The park-and-poll contract end to end: 202 with a pending session, the
    binder opens it when compute registers, and GET follows it to `open`."""
    from api.services.compute import service as compute_service

    monkeypatch.setattr(settings, "sql_session_wait_timeout_s", 0.2)

    resp = await authed_client.post(
        f"/workspaces/{workspace.slug}/sql/sessions", json={"on_wait_timeout": "continue"}
    )
    assert resp.status_code == 202
    session_id = resp.json()["id"]

    # The provisioned agent dials home: registration binds and opens the session.
    async def fake_dispatch(db, session, catalogs):
        session.status = "open"
        await db.commit()
        return True

    monkeypatch.setattr(session_service, "dispatch_open_session", fake_dispatch)
    agent_row = [a for a in await _agent_rows(db_session) if a.provider is not None][0]
    agent_row.lifecycle = "running"
    agent_row.capabilities = {"extensions": ["httpfs"]}
    await db_session.commit()
    assert await compute_service.bind_pending_sessions(db_session, agent_row) == 1

    resp = await authed_client.get(f"/sql/sessions/{session_id}")
    assert resp.status_code == 200
    assert resp.json()["status"] == "open"


async def test_wait_timeout_zero_with_cancel_is_rejected(
    authed_client, workspace, enabled, elastic_enabled
):
    resp = await authed_client.post(
        f"/workspaces/{workspace.slug}/sql/sessions",
        json={"wait_timeout_s": 0, "on_wait_timeout": "cancel"},
    )
    assert resp.status_code == 422


async def test_wait_timeout_above_the_cap_is_rejected(
    authed_client, workspace, enabled, elastic_enabled, monkeypatch
):
    monkeypatch.setattr(settings, "sql_session_max_wait_timeout_s", 120.0)
    resp = await authed_client.post(
        f"/workspaces/{workspace.slug}/sql/sessions", json={"wait_timeout_s": 121}
    )
    assert resp.status_code == 422


async def test_warm_open_is_unchanged(
    authed_client, workspace, connected_agent, enabled, elastic_enabled, monkeypatch
):
    """The backwards-compatibility pin: a client that sends none of the new fields
    against warm compute sees exactly the old 201, on the old budget."""
    seen: dict = {}

    async def fake_dispatch(db, session, catalogs):
        session.status = "open"
        await db.commit()
        return True

    async def fake_await(db, session, timeout_s, *args, **kwargs):
        seen["timeout_s"] = timeout_s
        return session

    monkeypatch.setattr(session_service, "dispatch_open_session", fake_dispatch)
    monkeypatch.setattr(session_service, "await_session_open", fake_await)

    resp = await authed_client.post(
        f"/workspaces/{workspace.slug}/sql/sessions", json={"agent_id": str(connected_agent.id)}
    )
    assert resp.status_code == 201
    assert resp.json()["status"] == "open"
    assert seen["timeout_s"] == settings.sql_session_open_timeout_s


async def test_statement_on_a_pending_session_is_409(
    authed_client, db_session, workspace, user, enabled, elastic_enabled
):
    session = SqlSession(
        workspace_id=workspace.id,
        agent_id=None,
        user_id=user.id,
        status="pending",
        active_catalog="test_ws",
    )
    db_session.add(session)
    await db_session.commit()

    resp = await authed_client.post(
        f"/sql/sessions/{session.id}/statements", json={"sql": "SELECT 1"}
    )
    assert resp.status_code == 409
    assert "pending" in resp.json()["message"]


async def test_no_agent_with_elastic_disabled_still_503s(authed_client, workspace, enabled):
    """Unchanged for a deployment that does not run elastic compute at all."""
    resp = await authed_client.post(f"/workspaces/{workspace.slug}/sql/sessions", json={})
    assert resp.status_code == 503
    assert resp.json()["message"] == "No connected agent available"
