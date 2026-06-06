"""Agent registration, capability reporting, and disconnect over the live
control channel between the real API and real agent processes.
"""

from __future__ import annotations

import asyncio

import pytest

pytestmark = pytest.mark.cross_component


async def test_agent_registers_and_reports_capabilities(healthy_agent) -> None:
    caps = healthy_agent["capabilities"]
    assert caps is not None
    # The bundled agent advertises the extensions object_store workspaces need.
    assert "httpfs" in caps["extensions"]
    assert "iceberg" in caps["extensions"]
    assert caps["duckdb_version"]
    assert caps["cores"] >= 1


async def test_agent_disconnect_marks_unavailable(api_client, spawn_agent) -> None:
    """A disposable agent appears healthy, then flips to unavailable once its
    process dies — exercising the WS-disconnect cleanup in the API."""
    before = {a["id"] for a in (await api_client.get("/api/agents")).json()}

    proc = spawn_agent()

    async def _new_healthy_id() -> str | None:
        agents = (await api_client.get("/api/agents")).json()
        new = [a for a in agents if a["id"] not in before and a["status"] == "healthy"]
        return new[0]["id"] if new else None

    agent_id = await _poll(_new_healthy_id, timeout=90.0)
    assert agent_id is not None, "spawned agent never registered healthy"

    proc.terminate()
    proc.wait(timeout=10)

    async def _is_unavailable() -> bool:
        agents = {a["id"]: a for a in (await api_client.get("/api/agents")).json()}
        return agents.get(agent_id, {}).get("status") == "unavailable"

    assert await _poll(_is_unavailable, timeout=30.0), "agent stayed healthy after disconnect"


async def _poll(predicate, timeout: float, interval: float = 0.5):
    """Await ``predicate`` until it returns a truthy value or timeout."""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        result = await predicate()
        if result:
            return result
        await asyncio.sleep(interval)
    return None
