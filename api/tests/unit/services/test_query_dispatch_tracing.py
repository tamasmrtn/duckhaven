"""dispatch_query injects a W3C trace_context into the DISPATCH_QUERY frame."""

import json
import uuid

import pytest
from conftest import seed_workspace

from api.models.agent import Agent
from api.models.query import Query
from api.models.user import User
from api.services.agent_registry import registry
from api.services.auth import hash_password
from api.services.query import dispatch_query


class FakeWS:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_text(self, payload: str) -> None:
        self.sent.append(json.loads(payload))


@pytest.fixture(autouse=True)
def _clear_registry():
    yield
    for cid in list(registry.connected_ids()):
        registry.unregister(uuid.UUID(cid))


async def test_dispatch_injects_trace_context(db_session, span_exporter):
    user = User(
        email="dispatcher@test.local", password_hash=hash_password("pw"), name="D", role="user"
    )
    db_session.add(user)
    await db_session.flush()
    ws, _catalog = await seed_workspace(db_session, user_id=user.id, slug="trace-ws")

    agent = Agent(name="a", status="healthy", capabilities={"extensions": ["httpfs", "iceberg"]})
    db_session.add(agent)
    await db_session.flush()
    ws_obj = FakeWS()
    registry.register(agent.id, ws_obj)  # type: ignore[arg-type]

    query = Query(workspace_id=ws.id, agent_id=agent.id, user_id=user.id, sql="SELECT 1")
    db_session.add(query)
    await db_session.flush()

    await dispatch_query(db_session, query)

    frame = ws_obj.sent[-1]
    assert frame["type"] == "dispatch_query"
    traceparent = frame["trace_context"]["traceparent"]

    spans = span_exporter.get_finished_spans()
    dispatch_spans = [s for s in spans if s.name == "dispatch_query"]
    assert len(dispatch_spans) == 1
    span = dispatch_spans[0]
    trace_id_hex = format(span.context.trace_id, "032x")
    assert trace_id_hex in traceparent
    assert span.attributes["duckhaven.query_id"] == str(query.id)
