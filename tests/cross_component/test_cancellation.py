"""Query cancellation and unavailable-agent behaviour across API + agent.

`cancel_query` signals the agent over the control channel and transitions the
query to ``cancelled`` in one step, so the state assertion is deterministic even
if the agent finishes the (deliberately heavy) query first.
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.cross_component

# Heavy enough to plausibly still be running when we cancel; correctness of the
# assertion does not depend on the race (cancel_query forces 'cancelled').
_SLOW_SQL = "SELECT count(*) FROM range(500000000) t(i)"


async def test_cancel_transitions_to_cancelled(api_client, workspace, healthy_agent) -> None:
    created = await api_client.post(
        f"/api/workspaces/{workspace}/queries",
        json={"sql": _SLOW_SQL, "agent_id": healthy_agent["id"]},
    )
    assert created.status_code == 202, created.text
    query_id = created.json()["id"]

    cancelled = await api_client.delete(f"/api/queries/{query_id}")
    assert cancelled.status_code == 204

    body = (await api_client.get(f"/api/queries/{query_id}")).json()
    assert body["status"] == "cancelled"


async def test_unknown_agent_is_rejected(api_client, workspace) -> None:
    resp = await api_client.post(
        f"/api/workspaces/{workspace}/queries",
        json={"sql": "SELECT 1", "agent_id": str(uuid.uuid4())},
    )
    assert resp.status_code == 404
