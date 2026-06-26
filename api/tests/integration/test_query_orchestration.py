"""Query submission + state transitions through the real control plane.

These exercise the orchestration path (guard → agent resolution → dispatch →
state) against real Postgres + Polaris, with a *stub* agent socket registered in
the live registry. Real agent execution and result delivery are Layer 2.
"""

from __future__ import annotations

import json
from uuid import uuid4

import pytest
import pytest_asyncio

from api.models.agent import Agent

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def workspace_slug(admin_client, workspace_factory) -> str:
    ws = await workspace_factory(name="Query Orchestration")
    slug = ws["slug"]
    # Workspaces no longer auto-create a catalog; attach a default one so queries
    # have a catalog to run against.
    created = await admin_client.post(
        f"/workspaces/{slug}/catalogs", json={"name": f"c_{slug.replace('-', '_')}"}
    )
    assert created.status_code == 201, created.text
    return slug


async def test_valid_query_is_dispatched(admin_client, workspace_slug, connected_agent) -> None:
    agent, stub = connected_agent
    resp = await admin_client.post(
        f"/workspaces/{workspace_slug}/queries",
        json={"sql": "SELECT 1", "agent_id": str(agent.id)},
    )
    assert resp.status_code == 202, resp.text
    body = resp.json()
    # Status stays "queued" until the agent admits the query and emits
    # QUERY_PROGRESS; the agent may hold it in its admission queue first.
    assert body["status"] == "queued"

    # The agent received exactly one DISPATCH_QUERY frame for this query, carrying
    # the workspace's catalog descriptors + the active catalog (multi-attach).
    assert len(stub.sent) == 1
    frame = json.loads(stub.sent[0])
    assert frame["type"] == "dispatch_query"
    assert frame["payload"]["sql"] == "SELECT 1"
    cats = frame["payload"]["catalogs"]
    assert len(cats) == 1
    assert cats[0]["backend"]["kind"] == "object_store"
    assert frame["payload"]["active_catalog"] == cats[0]["slug"]

    got = await admin_client.get(f"/queries/{body['id']}")
    assert got.status_code == 200
    assert got.json()["id"] == body["id"]


async def test_disallowed_sql_is_rejected(admin_client, workspace_slug, connected_agent) -> None:
    agent, stub = connected_agent
    resp = await admin_client.post(
        f"/workspaces/{workspace_slug}/queries",
        json={"sql": "SET memory_limit='1GB'", "agent_id": str(agent.id)},
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "sql_not_allowed"
    assert stub.sent == []  # never dispatched


async def test_unknown_agent_is_404(admin_client, workspace_slug) -> None:
    resp = await admin_client.post(
        f"/workspaces/{workspace_slug}/queries",
        json={"sql": "SELECT 1", "agent_id": str(uuid4())},
    )
    assert resp.status_code == 404


async def test_disconnected_agent_is_503(admin_client, db_session, workspace_slug) -> None:
    # An agent row that exists but is not in the live registry.
    agent = Agent(name="offline", status="unavailable", capabilities={"extensions": ["httpfs"]})
    db_session.add(agent)
    await db_session.commit()
    await db_session.refresh(agent)

    resp = await admin_client.post(
        f"/workspaces/{workspace_slug}/queries",
        json={"sql": "SELECT 1", "agent_id": str(agent.id)},
    )
    assert resp.status_code == 503


async def test_cancel_transitions_state_and_signals_agent(
    admin_client, workspace_slug, connected_agent
) -> None:
    agent, stub = connected_agent
    created = await admin_client.post(
        f"/workspaces/{workspace_slug}/queries",
        json={"sql": "SELECT 1", "agent_id": str(agent.id)},
    )
    query_id = created.json()["id"]

    cancelled = await admin_client.delete(f"/queries/{query_id}")
    assert cancelled.status_code == 204

    # A CANCEL_QUERY frame followed the dispatch, and state moved to cancelled.
    types = [json.loads(f)["type"] for f in stub.sent]
    assert types == ["dispatch_query", "cancel_query"]
    assert (await admin_client.get(f"/queries/{query_id}")).json()["status"] == "cancelled"
