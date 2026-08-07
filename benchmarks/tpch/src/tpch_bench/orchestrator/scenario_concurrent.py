"""Concurrent scenario (plan §4, config/scenarios.yaml): all 22 queries
fired in parallel.

Every real client this harness wraps (`duckhaven-sql-connector`,
`snowflake-connector-python`, `databricks-sql-connector`) is a blocking,
one-statement-at-a-time-per-connection DB-API client, not internally
thread-safe to run two queries on at once — see clients/base.py's module
docstring. So concurrency here comes from a `ThreadPoolExecutor` where
each worker opens and owns its *own* connection via `client_factory()`,
never sharing one `EngineClient` across threads.

The ledger and WAL, on the other hand, *are* shared across those worker
threads (one results store per run, not per worker) — this function builds
its own `threading.Lock` and derives a copy of `ctx` carrying it
(`RunContext.ledger_lock`), so every ledger/WAL write made through that
copy is serialized without the caller having to know this scenario, alone
among the three, needs one. Only the bookkeeping is serialized, not the
query execution itself, so the parallelism this scenario is measuring
stays real.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace

from tpch_bench.clients.base import EngineClient
from tpch_bench.orchestrator.runner import (
    RunContext,
    pending_query_work_items,
    register_query_work_items,
    run_query_work_item,
)

SCENARIO = "concurrent"


def run(
    ctx: RunContext,
    client_factory: Callable[[], EngineClient],
    queries: dict[str, str],
    *,
    reps: int,
    max_workers: int | None = None,
) -> None:
    items = register_query_work_items(ctx, scenario=SCENARIO, query_ids=list(queries), reps=reps)
    pending = pending_query_work_items(ctx, items)
    if not pending:
        return
    ctx = replace(ctx, ledger_lock=threading.Lock())

    def _run_one(item_id: str, query_id: str) -> None:
        client = client_factory()
        try:
            client.connect()
            run_query_work_item(ctx, client, item_id=item_id, sql=queries[query_id])
        finally:
            client.close()

    with ThreadPoolExecutor(max_workers=max_workers or len(pending)) as pool:
        futures = [
            pool.submit(_run_one, item_id, query_id)
            for item_id, (query_id, _rep) in pending.items()
        ]
        for future in as_completed(futures):
            future.result()  # re-raise a worker's exception on the caller's thread
