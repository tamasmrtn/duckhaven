"""Sequential scenario (plan §4, config/scenarios.yaml): all 22 standard
TPC-H queries, run one at a time on a single warm connection.
"""

from __future__ import annotations

from tpch_bench.clients.base import EngineClient
from tpch_bench.orchestrator.runner import (
    RunContext,
    pending_query_work_items,
    register_query_work_items,
    run_query_work_item,
)

SCENARIO = "sequential"


def run(ctx: RunContext, client: EngineClient, queries: dict[str, str], *, reps: int) -> None:
    """`queries`: {query_id: sql}, already in `client`'s own engine dialect
    (queries/dialect/<engine>/qNN.sql). One connection for the whole
    scenario: `client.connect()` is idempotent, so calling it once up
    front (rather than per query) is what makes this scenario "warm" as
    opposed to `scenario_cold_start`.
    """
    items = register_query_work_items(ctx, scenario=SCENARIO, query_ids=list(queries), reps=reps)
    pending = pending_query_work_items(ctx, items)
    if not pending:
        return
    client.connect()
    for item_id, (query_id, _rep) in pending.items():
        run_query_work_item(ctx, client, item_id=item_id, sql=queries[query_id])
