"""Concurrent scenario (plan §4, config/scenarios.yaml): all 22 queries
fired in parallel, repeated `reps` times.

Each rep is its own round, run to completion before the next starts —
`reps=3` means three 22-way bursts, never one 66-way burst. Collapsing
every rep into a single `ThreadPoolExecutor` submission was a real bug
this scenario had until a SF10 run against DuckHaven's fixed-size local
agent (METHODOLOGY.md §7) crashed under it: `pending_query_work_items`
returns every not-yet-done item across every rep, and submitting all of
them at once silently multiplied the intended concurrency by `reps` —
22-way turned into 66-way, well past what "all 22 queries fired at once"
was ever meant to describe.

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
    ctx = replace(ctx, ledger_lock=threading.Lock())

    def _run_one(item_id: str, query_id: str) -> None:
        client = client_factory()
        try:
            client.connect()
            run_query_work_item(ctx, client, item_id=item_id, sql=queries[query_id])
        finally:
            client.close()

    for rep in range(reps):
        rep_items = {iid: qr for iid, qr in items.items() if qr[1] == rep}
        pending = pending_query_work_items(ctx, rep_items)
        if not pending:
            continue
        with ThreadPoolExecutor(max_workers=max_workers or len(pending)) as pool:
            futures = [
                pool.submit(_run_one, item_id, query_id)
                for item_id, (query_id, _rep) in pending.items()
            ]
            for future in as_completed(futures):
                future.result()  # re-raise a worker's exception on the caller's thread
