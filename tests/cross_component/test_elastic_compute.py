"""Elastic compute end to end: work arriving with no compute brings it up.

The unit suites cover each link of this on its own — the scale-out primitive, the
binders, the routers — with the agent stubbed. What only this layer can show is
the whole chain running for real: a request finds no compute, the control plane
creates an agent row and mints its bootstrap token, an actual agent process dials
home over the WebSocket, the API revives *that* row rather than minting a new one,
the registration hook binds the work parked for it, and the run or session
completes on it.

Every path that admits work is exercised: a pool query, a SQL session (which
cannot park silently, because its caller is blocked on the open), and both of
those naming an agent the idle reaper has already terminated.

The `null` compute backend is the seam. It creates no instance, so the test starts
the agent process itself with the token the control plane minted — see
`start_provisioned_agent`. Everything on the control-plane side of that seam is
the production code path.
"""

from __future__ import annotations

import asyncio

import pytest

pytestmark = pytest.mark.cross_component


async def _poll(fn, timeout: float = 60.0, interval: float = 0.5):
    """Poll ``fn`` until it returns something truthy, or give up."""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        result = await fn()
        if result:
            return result
        await asyncio.sleep(interval)
    return None


async def _provisioning_agent(client) -> dict | None:
    agents = (await client.get("/api/admin/agents")).json()
    return next((a for a in agents if a.get("lifecycle") == "provisioning"), None)


async def _agent(client, agent_id: str) -> dict:
    return (await client.get(f"/api/admin/agents/{agent_id}")).json()


async def _finished(client, query_id: str) -> dict | None:
    body = (await client.get(f"/api/queries/{query_id}")).json()
    return body if body["status"] in ("done", "failed", "cancelled") else None


async def _bring_up_compute(client, start_provisioned_agent, timeout: float = 60.0) -> dict:
    """Wait for a row to appear in `provisioning`, start its agent, wait for healthy."""
    row = await _poll(lambda: _provisioning_agent(client), timeout=timeout)
    assert row is not None, "no elastic agent was provisioned"
    await start_provisioned_agent(row["id"])
    healthy = await _poll(lambda: _agent_healthy(client, row["id"]), timeout=timeout)
    assert healthy, f"provisioned agent {row['id']} never registered healthy"
    return healthy


async def _agent_healthy(client, agent_id: str) -> dict | None:
    row = await _agent(client, agent_id)
    return row if row.get("status") == "healthy" and row.get("lifecycle") == "running" else None


async def _terminate_and_kill(client, agent_id: str, proc) -> None:
    """Scale in for real: destroy the "instance" *and* the row.

    Killing the process is the part the null backend cannot do — with a real
    backend, terminating the row destroys the container and the agent goes with
    it. Without this the agent would simply reconnect on its session token and the
    row would never be observed as terminated.
    """
    resp = await client.post(f"/api/admin/agents/{agent_id}/terminate")
    assert resp.status_code == 202, resp.text
    proc.terminate()
    proc.wait(timeout=15)
    gone = await _poll(lambda: _terminated(client, agent_id), timeout=30.0)
    assert gone, f"agent {agent_id} never reached terminated"


async def _terminated(client, agent_id: str) -> dict | None:
    row = await _agent(client, agent_id)
    return row if row.get("lifecycle") == "terminated" else None


# ── Pool: no agent named ──────────────────────────────────────────────────────


async def test_pool_query_parks_then_runs_when_compute_arrives(
    elastic_client, elastic_workspace, start_provisioned_agent
) -> None:
    """The scale-out path that already existed, proven end to end for the first time."""
    resp = await elastic_client.post(
        f"/api/workspaces/{elastic_workspace}/queries", json={"sql": "SELECT 1 AS n"}
    )
    assert resp.status_code == 202, resp.text
    query = resp.json()
    # Parked: no compute to run it on yet, and the client just polls as usual.
    assert query["status"] == "queued"
    assert query["agent_id"] is None

    agent = await _bring_up_compute(elastic_client, start_provisioned_agent)

    done = await _poll(lambda: _finished(elastic_client, query["id"]), timeout=90.0)
    assert done is not None, "parked query never finished after compute arrived"
    assert done["status"] == "done", done
    # It ran on the agent the control plane provisioned for it.
    assert done["agent_id"] == agent["id"]

    rows = (await elastic_client.get(f"/api/queries/{query['id']}/rows")).json()
    assert rows["rows"] == [{"n": 1}]


async def test_session_cold_start_opens_when_compute_arrives(
    elastic_client, elastic_workspace, start_provisioned_agent
) -> None:
    """The gap this work closed: a session against a cold pool used to 503.

    This is the only test that drives the whole session cold-start chain for real —
    pending row, scale-out, the registration hook calling `bind_pending_sessions`,
    the open frame over the socket, and the agent's SESSION_OPENED coming back.
    """
    resp = await elastic_client.post(
        f"/api/workspaces/{elastic_workspace}/sql/sessions",
        json={"wait_timeout_s": 0, "on_wait_timeout": "continue"},
    )
    assert resp.status_code == 202, resp.text
    session = resp.json()
    assert session["status"] == "pending"
    assert session["agent_id"] is None
    # Catalog and staging prefix are workspace properties, so the session is fully
    # described before any agent exists to hold it.
    assert session["active_catalog"]
    assert session["staging_uri"]

    agent = await _bring_up_compute(elastic_client, start_provisioned_agent)

    async def _open() -> dict | None:
        row = (await elastic_client.get(f"/api/sql/sessions/{session['id']}")).json()
        return row if row["status"] == "open" else None

    opened = await _poll(_open, timeout=90.0)
    assert opened is not None, "pending session never opened after compute arrived"
    assert opened["agent_id"] == agent["id"]

    # And it is a usable session, not just an open row.
    stmt = await elastic_client.post(
        f"/api/sql/sessions/{session['id']}/statements", json={"sql": "SELECT 42 AS answer"}
    )
    assert stmt.status_code == 202, stmt.text
    done = await _poll(lambda: _finished(elastic_client, stmt.json()["id"]), timeout=90.0)
    assert done is not None and done["status"] == "done", done
    rows = (await elastic_client.get(f"/api/queries/{stmt.json()['id']}/rows")).json()
    assert rows["rows"] == [{"answer": 42}]


# ── Targeted: an agent the reaper already terminated ───────────────────────────


async def test_terminated_agent_restarts_for_a_targeted_query(
    elastic_client, elastic_workspace, start_provisioned_agent
) -> None:
    """Naming an idle-terminated agent starts it and waits, instead of failing.

    The reaper tears an elastic agent down precisely *because* nothing is using it,
    so refusing the next run that names it would make it permanently unusable.
    """
    first = await elastic_client.post(
        f"/api/workspaces/{elastic_workspace}/queries", json={"sql": "SELECT 1 AS n"}
    )
    assert first.status_code == 202
    row = await _poll(lambda: _provisioning_agent(elastic_client))
    assert row is not None
    proc = await start_provisioned_agent(row["id"])
    assert await _poll(lambda: _agent_healthy(elastic_client, row["id"]))
    agent_id = row["id"]

    await _terminate_and_kill(elastic_client, agent_id, proc)

    # Now name the terminated agent explicitly.
    resp = await elastic_client.post(
        f"/api/workspaces/{elastic_workspace}/queries",
        json={"sql": "SELECT 7 AS n", "agent_id": agent_id},
    )
    assert resp.status_code == 202, resp.text
    query = resp.json()
    assert query["status"] == "queued"
    # Parked, not bound: the binder claims it when the agent dials home.
    assert query["agent_id"] is None

    restarted = await _poll(lambda: _provisioning_agent(elastic_client))
    assert restarted is not None, "naming a terminated agent did not restart it"
    assert restarted["id"] == agent_id, "a different agent was provisioned"

    # The row is reused, so its bootstrap token must have been re-minted: starting
    # the agent with a stale token would be refused.
    await start_provisioned_agent(agent_id)
    assert await _poll(lambda: _agent_healthy(elastic_client, agent_id))

    done = await _poll(lambda: _finished(elastic_client, query["id"]), timeout=90.0)
    assert done is not None, "targeted run never finished after its agent restarted"
    assert done["status"] == "done", done
    assert done["agent_id"] == agent_id
    rows = (await elastic_client.get(f"/api/queries/{query['id']}/rows")).json()
    assert rows["rows"] == [{"n": 7}]


async def test_terminated_agent_restarts_for_a_targeted_session(
    elastic_client, elastic_workspace, start_provisioned_agent
) -> None:
    """The same for a session, which is how dbt and dlt name a specific agent."""
    first = await elastic_client.post(
        f"/api/workspaces/{elastic_workspace}/queries", json={"sql": "SELECT 1 AS n"}
    )
    assert first.status_code == 202
    row = await _poll(lambda: _provisioning_agent(elastic_client))
    assert row is not None
    proc = await start_provisioned_agent(row["id"])
    assert await _poll(lambda: _agent_healthy(elastic_client, row["id"]))
    agent_id = row["id"]

    await _terminate_and_kill(elastic_client, agent_id, proc)

    resp = await elastic_client.post(
        f"/api/workspaces/{elastic_workspace}/sql/sessions",
        json={"agent_id": agent_id, "wait_timeout_s": 0, "on_wait_timeout": "continue"},
    )
    assert resp.status_code == 202, resp.text
    session = resp.json()
    assert session["status"] == "pending"

    restarted = await _poll(lambda: _provisioning_agent(elastic_client))
    assert restarted is not None and restarted["id"] == agent_id
    await start_provisioned_agent(agent_id)

    async def _open() -> dict | None:
        r = (await elastic_client.get(f"/api/sql/sessions/{session['id']}")).json()
        return r if r["status"] == "open" else None

    opened = await _poll(_open, timeout=90.0)
    assert opened is not None, "session never opened on the restarted agent"
    assert opened["agent_id"] == agent_id
