"""Cold-start scenario (plan §4, config/scenarios.yaml): the same 22
queries, with compute cold-started before each one — isolates the
"resume from zero" cost each platform charges (an elastic DuckHaven agent
scaled to zero, a suspended Snowflake warehouse, a stopped Databricks
Serverless SQL Warehouse).

Achieved with an explicit `close()` + `connect()` cycle around every
single query, not by relying on `connect()`'s documented idempotence —
idempotence is what `scenario_sequential` leans on to stay warm; forcing a
fresh connection every time is the whole point here.
"""

from __future__ import annotations

from tpch_bench.clients.base import EngineClient
from tpch_bench.orchestrator.runner import (
    RunContext,
    pending_query_work_items,
    register_query_work_items,
    run_query_work_item,
)

SCENARIO = "cold_start"


def run(ctx: RunContext, client: EngineClient, queries: dict[str, str], *, reps: int) -> None:
    items = register_query_work_items(ctx, scenario=SCENARIO, query_ids=list(queries), reps=reps)
    pending = pending_query_work_items(ctx, items)
    for item_id, (query_id, _rep) in pending.items():
        client.close()
        client.connect()
        run_query_work_item(ctx, client, item_id=item_id, sql=queries[query_id])
