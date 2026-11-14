"""DISPATCH_BATCH across the real API and agent, and the time it saves.

Nothing submits a batch over HTTP yet (dashboards will), so the test plays a
second API replica: it runs ``dispatch_batch`` in this process against the shared
database, and the frame reaches the agent through the live API's ``/internal``
relay, as it would from any replica that does not hold the agent's socket. The
agent's progress and results come back through the live API as for any run.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

import pytest

pytestmark = pytest.mark.cross_component

N = 16
_TERMINAL = {"done", "failed", "cancelled"}


async def _batch(replica, api_client, workspace: str, agent_id: str, sqls: list[str]) -> list[str]:
    """Dispatch ``sqls`` as one batch, as the signed-in admin; their query ids."""
    from sqlalchemy import select

    from api.models.query import Query
    from api.models.workspace import Workspace
    from api.services import query as query_service

    user_id = uuid.UUID((await api_client.get("/api/me")).json()["id"])
    async with replica() as db:
        ws = (await db.execute(select(Workspace).where(Workspace.slug == workspace))).scalar_one()
        queries = [
            Query(
                workspace_id=ws.id,
                agent_id=uuid.UUID(agent_id),
                user_id=user_id,
                sql=sql,
                status="queued",
                started_at=datetime.now(UTC),
            )
            for sql in sqls
        ]
        db.add_all(queries)
        await db.flush()
        outcomes = await query_service.dispatch_batch(db, queries, timeout_s=120)
        assert outcomes == [None] * len(queries)
        return [str(q.id) for q in queries]


async def _wait(api_client, query_ids: list[str], timeout_s: float = 120) -> dict[str, dict]:
    runs: dict[str, dict] = {}
    async with asyncio.timeout(timeout_s):
        while len(runs) < len(query_ids):
            for query_id in query_ids:
                if query_id not in runs:
                    body = (await api_client.get(f"/api/queries/{query_id}")).json()
                    if body["status"] in _TERMINAL:
                        runs[query_id] = body
            await asyncio.sleep(0.05)
    return runs


async def _rows(api_client, query_id: str) -> list[dict]:
    return (await api_client.get(f"/api/queries/{query_id}/rows")).json()["rows"]


async def test_a_batch_runs_every_query_in_order(api_client, workspace, healthy_agent, replica):
    assert "batch_dispatch" in healthy_agent["capabilities"]["protocol_features"]
    sqls = [f"SELECT {i} AS n" for i in range(4)]

    batched = await _batch(replica, api_client, workspace, healthy_agent["id"], sqls)
    runs = await _wait(api_client, batched)

    assert [runs[q]["status"] for q in batched] == ["done"] * 4
    assert [await _rows(api_client, q) for q in batched] == [[{"n": i}] for i in range(4)]
    # One connection runs them one after another.
    finished = [runs[q]["finished_at"] for q in batched]
    assert finished == sorted(finished)


def _span_ms(runs: dict[str, dict]) -> float:
    """First submit to last finish, by the API's own clock."""
    started = min(datetime.fromisoformat(r["started_at"]) for r in runs.values())
    finished = max(datetime.fromisoformat(r["finished_at"]) for r in runs.values())
    return (finished - started).total_seconds() * 1000


async def _one_shot(api_client, workspace, agent_id, sqls, *, at_once: int) -> dict[str, dict]:
    """``sqls`` as one-shot queries, ``at_once`` in flight at a time."""
    gate = asyncio.Semaphore(at_once)

    async def run(sql: str) -> tuple[str, dict]:
        async with gate:
            created = await api_client.post(
                f"/api/workspaces/{workspace}/queries",
                json={"sql": sql, "agent_id": agent_id, "use_cache": False},
            )
            query_id = created.json()["id"]
            return query_id, (await _wait(api_client, [query_id]))[query_id]

    return dict(await asyncio.gather(*(run(sql) for sql in sqls)))


async def test_batch_timing(api_client, workspace, catalog, healthy_agent, replica):
    """Records what a batch costs against the same reads sent one by one.

    Printed, not asserted: the numbers depend on the machine. "4 at a time" is
    what a dashboard falls back to on an agent without batches.
    """
    agent_id = healthy_agent["id"]
    created = await api_client.post(
        f"/api/workspaces/{workspace}/catalogs/{catalog}/schemas/analytics/tables",
        json={"name": "kv", "columns": [{"name": "v", "type": "BIGINT", "nullable": True}]},
    )
    assert created.status_code == 201, created.text
    insert = await _one_shot(
        api_client, workspace, agent_id, ["INSERT INTO kv VALUES (1), (2)"], at_once=1
    )
    assert [r["status"] for r in insert.values()] == ["done"]

    workloads = {
        "trivial": lambda i: f"SELECT {i} AS n",
        "table": lambda i: f"SELECT count(*) + {i} AS n FROM {catalog}.analytics.kv",
    }
    lines = []
    for name, sql in workloads.items():
        spans = {}
        sqls = [sql(i) for i in range(N)]
        runs = await _wait(api_client, await _batch(replica, api_client, workspace, agent_id, sqls))
        spans["batch"] = runs
        spans["one-shot, all at once"] = await _one_shot(
            api_client, workspace, agent_id, [sql(N + i) for i in range(N)], at_once=N
        )
        spans["one-shot, 4 at a time"] = await _one_shot(
            api_client, workspace, agent_id, [sql(2 * N + i) for i in range(N)], at_once=4
        )
        for mode, runs in spans.items():
            assert {r["status"] for r in runs.values()} == {"done"}, (name, mode)
            lines.append(f"  {name:8} {mode:22} {_span_ms(runs):6.0f} ms")
    print(f"\n{N} reads, first submit to last finish:\n" + "\n".join(lines))


async def test_cancelling_the_running_query_moves_the_batch_on(
    api_client, workspace, healthy_agent, replica
):
    slow, quick = await _batch(
        replica,
        api_client,
        workspace,
        healthy_agent["id"],
        ["SELECT count(*) FROM range(10000000000000) t(i)", "SELECT 2 AS n"],
    )
    async with asyncio.timeout(60):
        while (await api_client.get(f"/api/queries/{slow}")).json()["status"] != "running":
            await asyncio.sleep(0.05)

    assert (await api_client.delete(f"/api/queries/{slow}")).status_code == 204

    runs = await _wait(api_client, [slow, quick], timeout_s=60)
    assert runs[slow]["status"] == "cancelled"
    assert runs[quick]["status"] == "done"
    assert await _rows(api_client, quick) == [{"n": 2}]
