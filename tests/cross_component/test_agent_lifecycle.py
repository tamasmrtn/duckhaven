"""Agent registration, capability reporting, and disconnect over the live
control channel between the real API and real agent processes.
"""

from __future__ import annotations

import asyncio
import stat

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
    # The agent's runtime survives the round trip: reported over the control
    # channel, judged by the API, and served back to the picker.
    assert caps["runtime_id"] == "1.5"
    assert caps["engine_version"].startswith("v1.5.")
    assert caps["sandbox"] in ("verified", "disabled")
    assert healthy_agent["runtime"]["id"] == "1.5"
    assert healthy_agent["runtime"]["state"] == "ok"


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


async def test_a_restarted_agent_moves_its_token_and_reconnects_as_itself(
    api_client, spawn_agent, tmp_path
) -> None:
    """A restart keeps the agent's identity, also when SESSION_TOKEN_PATH moves.

    The first run keeps its session token beside the results, the default. The
    restart points SESSION_TOKEN_PATH elsewhere, as the bundled compose file does:
    the agent moves the token there and reconnects with it rather than with the
    fresh bootstrap token it is also given, so it comes back as the same agent.
    """
    results = tmp_path / "results"
    results.mkdir()
    legacy = results / ".session-token"
    moved = tmp_path / "state" / "session-token"
    before = {a["id"] for a in (await api_client.get("/api/agents")).json()}

    async def _healthy_new_ids() -> set[str]:
        agents = (await api_client.get("/api/agents")).json()
        return {a["id"] for a in agents if a["id"] not in before and a["status"] == "healthy"}

    async def _token_saved() -> bool:
        return legacy.exists()

    proc = spawn_agent({"RESULTS_DIR": str(results)})
    first = await _poll(_healthy_new_ids, timeout=90.0)
    assert first is not None and len(first) == 1, "spawned agent never registered healthy"
    (agent_id,) = first
    assert await _poll(_token_saved, timeout=10.0), "the agent saved no session token"

    proc.terminate()
    proc.wait(timeout=10)

    async def _is_unavailable() -> bool:
        agents = {a["id"]: a for a in (await api_client.get("/api/agents")).json()}
        return agents.get(agent_id, {}).get("status") == "unavailable"

    assert await _poll(_is_unavailable, timeout=30.0), "agent stayed healthy after it stopped"

    spawn_agent({"RESULTS_DIR": str(results), "SESSION_TOKEN_PATH": str(moved)})
    back = await _poll(_healthy_new_ids, timeout=90.0)
    assert back == {agent_id}, "the restarted agent registered as a new agent"
    assert moved.exists() and not legacy.exists()
    assert stat.S_IMODE(moved.stat().st_mode) == 0o600


async def _poll(predicate, timeout: float, interval: float = 0.5):
    """Await ``predicate`` until it returns a truthy value or timeout."""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        result = await predicate()
        if result:
            return result
        await asyncio.sleep(interval)
    return None
