"""dispatch_batch: one person's reads go to an agent in one DISPATCH_BATCH frame,
each checked as dispatch_query checks it, with a per-query fallback."""

import uuid

import pytest
import pytest_asyncio
from conftest import seed_workspace
from prometheus_client import REGISTRY

from api.config import settings
from api.models.agent import Agent
from api.models.catalog import Catalog, WorkspaceCatalog
from api.models.query import Query
from api.models.storage_backend import StorageBackend
from api.models.user import User
from api.services import grants as grant_service
from api.services import query as query_service
from api.services.agent_registry import registry
from duckhaven_shared.protocol import Frame, FrameType

FEATURES = ["on_demand_attach", "use_schema", "batch_dispatch"]


class MockWebSocket:
    def __init__(self) -> None:
        self.sent: list[Frame] = []

    async def send_text(self, text: str) -> None:
        self.sent.append(Frame.model_validate_json(text))


@pytest_asyncio.fixture
async def user(db_session):
    u = User(email="b@batch.local", password_hash="x", name="Batcher", role="user")
    db_session.add(u)
    await db_session.commit()
    return u


@pytest_asyncio.fixture
async def workspace(db_session, user):
    """The default catalog `test_ws`, plus `sales`."""
    ws, _default = await seed_workspace(db_session, user_id=user.id)
    backend = StorageBackend(
        kind="object_store", name="sales-store", root_uri="/tmp/test", created_by=user.id
    )
    db_session.add(backend)
    await db_session.flush()
    sales = Catalog(
        slug="sales",
        name="sales",
        polaris_name="wh-sales",
        storage_backend_id=backend.id,
        created_by=user.id,
    )
    db_session.add(sales)
    await db_session.flush()
    db_session.add(
        WorkspaceCatalog(
            workspace_id=ws.id, catalog_id=sales.id, is_default=False, attached_by=user.id
        )
    )
    await db_session.commit()
    return ws


async def _agent(db, features=FEATURES):
    agent = Agent(
        name=f"agent-{uuid.uuid4().hex[:6]}",
        status="healthy",
        capabilities={
            "duckdb_version": "1.5.5",
            "extensions": ["httpfs", "iceberg"],
            "protocol_features": features,
        },
    )
    db.add(agent)
    await db.commit()
    sock = MockWebSocket()
    registry.register(agent.id, sock)  # type: ignore[arg-type]
    return agent, sock


@pytest.fixture(autouse=True)
def _clear_registry():
    yield
    for cid in list(registry.connected_ids()):
        registry.unregister(uuid.UUID(cid))


async def _queries(db, workspace, user, agent, *sqls, **values) -> list[Query]:
    rows = [
        Query(workspace_id=workspace.id, agent_id=agent.id, user_id=user.id, sql=sql, **values)
        for sql in sqls
    ]
    db.add_all(rows)
    await db.flush()
    return rows


def _submitted() -> float:
    labels = {"replica_id": settings.replica_id}
    return REGISTRY.get_sample_value("duckhaven_queries_submitted_total", labels) or 0


async def test_reads_go_in_one_frame_with_every_catalog_they_name(
    db_session, workspace, user, monkeypatch
):
    activity = []

    async def record_activity(db, agent_id):
        activity.append(agent_id)

    monkeypatch.setattr("api.services.compute.service.record_activity", record_activity)
    agent, sock = await _agent(db_session)
    queries = await _queries(
        db_session, workspace, user, agent, "SELECT 1", "SELECT * FROM sales.analytics.orders"
    )
    queries[1].timeout_s = 30.0
    queries[1].origin = "dashboard"
    before = _submitted()

    outcomes = await query_service.dispatch_batch(db_session, queries, timeout_s=60)

    assert outcomes == [None, None]
    [frame] = sock.sent
    assert frame.type == FrameType.DISPATCH_BATCH
    assert frame.payload["active_catalog"] == "test_ws"
    assert frame.payload["timeout_s"] == 60
    assert frame.payload["queries"] == [
        {"query_id": str(queries[0].id), "sql": "SELECT 1"},
        {"query_id": str(queries[1].id), "sql": queries[1].sql, "timeout_s": 30.0},
    ]
    assert [c["slug"] for c in frame.payload["catalogs"]] == ["test_ws", "sales"]
    assert sorted(frame.payload["workspace_catalogs"]) == ["sales", "test_ws"]
    assert frame.payload["batch_id"]
    # Each row is stamped as a dispatched one is.
    assert all(q.active_catalog == "test_ws" and q.runtime_id for q in queries)
    # The agent is marked busy once; every interactive run, a dashboard's too, is counted.
    assert activity == [agent.id]
    assert _submitted() == before + 2


async def test_a_refused_query_is_left_out_and_reported(db_session, workspace, user, monkeypatch):
    real_check = grant_service.assert_query_access

    async def check(db, workspace_id, principal_id, sql, *args, **kwargs):
        if "secret" in sql:
            raise grant_service.GrantDenied("Not authorized on secret")
        return await real_check(db, workspace_id, principal_id, sql, *args, **kwargs)

    monkeypatch.setattr(grant_service, "assert_query_access", check)
    agent, sock = await _agent(db_session)
    queries = await _queries(
        db_session, workspace, user, agent, "SELECT 1", "SELECT * FROM secret", "SELECT 3"
    )

    outcomes = await query_service.dispatch_batch(db_session, queries)

    assert outcomes[0] is None and outcomes[2] is None
    assert isinstance(outcomes[1], grant_service.GrantDenied)
    [frame] = sock.sent
    assert [q["sql"] for q in frame.payload["queries"]] == ["SELECT 1", "SELECT 3"]


async def test_a_long_batch_is_sent_in_parts(db_session, workspace, user, monkeypatch):
    monkeypatch.setattr(query_service, "BATCH_MAX_QUERIES", 2)
    agent, sock = await _agent(db_session)
    queries = await _queries(db_session, workspace, user, agent, "SELECT 1", "SELECT 2", "SELECT 3")

    assert await query_service.dispatch_batch(db_session, queries) == [None] * 3

    assert [len(f.payload["queries"]) for f in sock.sent] == [2, 1]
    assert len({f.payload["batch_id"] for f in sock.sent}) == 2


async def test_a_chosen_schema_is_used_on_the_active_catalog(db_session, workspace, user):
    agent, sock = await _agent(db_session)
    queries = await _queries(db_session, workspace, user, agent, "SELECT 1", "SELECT 2")

    await query_service.dispatch_batch(db_session, queries, active_schema="marts")

    [active] = [c for c in sock.sent[0].payload["catalogs"] if c["slug"] == "test_ws"]
    assert active["use_schema"] == "marts"
    assert all(q.active_schema == "marts" for q in queries)


async def test_an_agent_without_the_feature_gets_one_query_at_a_time(db_session, workspace, user):
    agent, sock = await _agent(db_session, features=["on_demand_attach"])
    queries = await _queries(db_session, workspace, user, agent, "SELECT 1", "SELECT 2")

    assert await query_service.dispatch_batch(db_session, queries) == [None, None]

    assert [f.type for f in sock.sent] == [FrameType.DISPATCH_QUERY] * 2
    assert [f.payload["query_id"] for f in sock.sent] == [str(q.id) for q in queries]


async def test_a_frame_that_cannot_be_sent_falls_back_to_one_at_a_time(
    db_session, workspace, user, monkeypatch
):
    agent, sock = await _agent(db_session)
    real_send = query_service.send_to_agent

    async def send(db, agent_id, text):
        if Frame.model_validate_json(text).type == FrameType.DISPATCH_BATCH:
            return False
        return await real_send(db, agent_id, text)

    monkeypatch.setattr(query_service, "send_to_agent", send)
    queries = await _queries(db_session, workspace, user, agent, "SELECT 1", "SELECT 2")

    assert await query_service.dispatch_batch(db_session, queries) == [None, None]

    assert [f.type for f in sock.sent] == [FrameType.DISPATCH_QUERY] * 2


async def test_an_agent_that_is_gone_refuses_every_query(db_session, workspace, user):
    agent, _ = await _agent(db_session)
    registry.unregister(agent.id)
    queries = await _queries(db_session, workspace, user, agent, "SELECT 1", "SELECT 2")

    outcomes = await query_service.dispatch_batch(db_session, queries)

    assert all(isinstance(o, query_service.AgentUnavailable) for o in outcomes)


async def test_only_one_persons_reads_are_batched(db_session, workspace, user):
    agent, sock = await _agent(db_session)
    other = User(email="o@batch.local", password_hash="x", name="Other", role="user")
    db_session.add(other)
    await db_session.flush()
    mixed = [
        *await _queries(db_session, workspace, user, agent, "SELECT 1"),
        *await _queries(db_session, workspace, other, agent, "SELECT 2"),
    ]
    writes = await _queries(db_session, workspace, user, agent, "SELECT 1", "DROP TABLE t")

    with pytest.raises(ValueError, match="one person"):
        await query_service.dispatch_batch(db_session, mixed)
    with pytest.raises(ValueError, match="Only reads"):
        await query_service.dispatch_batch(db_session, writes)
    assert sock.sent == []
    assert await query_service.dispatch_batch(db_session, []) == []
