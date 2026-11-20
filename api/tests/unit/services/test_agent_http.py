"""The shared HTTP client for agent result servers and peer replicas."""

from __future__ import annotations

import httpx
import pytest

from api.services import agent_http
from api.services import query as query_service


@pytest.fixture(autouse=True)
async def _fresh_client():
    await agent_http.aclose()
    yield
    await agent_http.aclose()


async def test_one_client_is_reused_across_calls():
    """Building a client per call cost about 2.8 ms of CPU each on the event loop."""
    first = agent_http.client()
    assert agent_http.client() is first
    assert not first.is_closed


async def test_a_closed_client_is_replaced():
    first = agent_http.client()
    await first.aclose()
    assert agent_http.client() is not first


async def test_a_client_from_another_event_loop_is_not_reused(monkeypatch):
    """A connection pool cannot cross event loops; each test runs its own."""
    first = agent_http.client()
    monkeypatch.setattr(agent_http, "_loop", object())
    assert agent_http.client() is not first


async def test_aclose_closes_the_shared_client():
    client = agent_http.client()
    await agent_http.aclose()
    assert client.is_closed


async def test_a_failed_result_stream_leaves_the_shared_client_open(monkeypatch):
    """The stream's caller closes the response only: the client outlives it."""
    from fastapi import HTTPException

    from api.models.agent import Agent
    from api.models.query import Query

    shared = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(404)))
    monkeypatch.setattr(agent_http, "client", lambda: shared)
    agent = Agent(name="a", status="healthy", result_host="127.0.0.1", result_port=8001)
    query = Query(sql="SELECT 1", status="done")
    source = query_service.ResultSource(query=query, agent=agent, token="t")

    with pytest.raises(HTTPException) as exc:
        await query_service.open_result_stream(source)
    assert exc.value.status_code == 410
    assert not shared.is_closed
    await shared.aclose()
