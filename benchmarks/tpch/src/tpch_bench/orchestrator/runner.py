"""Shared work-item/WAL/ledger plumbing every scenario needs identically
(plan §3/§6): register the full expected work-item set, skip whatever the
ledger already shows as `done` (the anti-join resumption model), execute
the rest, and record every outcome.

Two different orderings apply to the two things this module writes to the
WAL, both deliberate:

- Result rows (`_record_result`): the full row is known entirely from the
  `QueryResult` the engine client returned, so it is written to the WAL
  *before* the ledger — a crash between the two loses nothing, the next
  startup's WAL replay (`ledger.upsert_from_wal`) catches the ledger up.
- Status-transition rows (`_mark`): `Ledger.mark_running/done/failed` own
  the attempt-count and timestamp bookkeeping for a transition (see
  `ledger/store.py`), and now return the exact row they wrote. Recomputing
  that here to write it to the WAL first would duplicate logic that only
  belongs in one place; this module logs it right after instead. The
  crash window this leaves (process dies between the ledger write and the
  WAL write) is bounded to "the WAL doesn't yet show a transition the
  ledger already has" — recoverable by trusting the ledger's own state,
  which is exactly what it's for.

Registration itself (`register_work_item`) never goes to the WAL: it's an
`ON CONFLICT DO NOTHING` identity-ensure, cheap and safe to redo from
`config/*.yaml` on every restart, and — critically — `upsert_from_wal`
routes every `work_items` WAL row through `_set_state` (`DO UPDATE`), so a
naive "pending" snapshot logged at registration time would silently reset
already-`done` work back to `pending` on replay. Only a real transition
belongs in the WAL's `work_items` events.
"""

from __future__ import annotations

import threading
from contextlib import nullcontext
from dataclasses import dataclass
from typing import Any

from tpch_bench.clients.base import EngineClient, QueryResult
from tpch_bench.ledger.store import Ledger, work_item_id
from tpch_bench.ledger.wal import WalWriter

_RESULT_FIELDS = (
    "engine_query_id",
    "server_duration_ms",
    "queued_ms",
    "execution_ms",
    "client_wall_ms",
    "row_count",
    "bytes_scanned",
    "peak_memory_bytes",
    "spill_bytes",
    "compute_ref",
    "error",
    "raw_response_json",
)


@dataclass(frozen=True)
class RunContext:
    """Everything a scenario needs to execute and record its work, bundled
    once per (engine, scale_factor, scenario) invocation."""

    ledger: Ledger
    wal: WalWriter
    engine: str
    scale_factor: int
    run_id: str
    methodology_hash: str
    query_timeout_s: float
    # Set by the `concurrent` scenario, whose workers share this RunContext's
    # ledger/WAL across threads while each holding their own EngineClient
    # connection: DuckDB connections and a plain file handle are not safe to
    # write from multiple threads at once, so every ledger/WAL write here
    # takes this lock. None (the default) for the single-threaded scenarios,
    # where there is nothing to contend over.
    ledger_lock: threading.Lock | None = None


def query_work_item_id(ctx: RunContext, *, scenario: str, query_id: str, rep: int) -> str:
    return work_item_id(
        kind="query",
        engine=ctx.engine,
        scale_factor=ctx.scale_factor,
        scenario=scenario,
        query_id=query_id,
        rep=rep,
    )


def register_query_work_items(
    ctx: RunContext, *, scenario: str, query_ids: list[str], reps: int
) -> dict[str, tuple[str, int]]:
    """Register the full expected (query_id, rep) work-item set for this
    scenario. Returns {work_item_id: (query_id, rep)}, in (query_id, rep)
    order, so a scenario loop can drive off it directly."""
    items: dict[str, tuple[str, int]] = {}
    for query_id in query_ids:
        for rep in range(reps):
            item_id = query_work_item_id(ctx, scenario=scenario, query_id=query_id, rep=rep)
            items[item_id] = (query_id, rep)
            ctx.ledger.register_work_item(
                work_item_id=item_id,
                kind="query",
                engine=ctx.engine,
                scale_factor=ctx.scale_factor,
                scenario=scenario,
                query_id=query_id,
                rep=rep,
                run_id=ctx.run_id,
                methodology_hash=ctx.methodology_hash,
            )
    return items


def pending_query_work_items(
    ctx: RunContext, items: dict[str, tuple[str, int]]
) -> dict[str, tuple[str, int]]:
    """`items` (as `register_query_work_items` returned it), narrowed to
    the ids that aren't `done` yet — a resumed run's whole reason for
    calling `register_query_work_items` again is to get back down to this
    set without touching the engine client for work already finished."""
    pending_ids = set(ctx.ledger.pending_work_item_ids(list(items)))
    return {item_id: qr for item_id, qr in items.items() if item_id in pending_ids}


def run_query_work_item(
    ctx: RunContext, client: EngineClient, *, item_id: str, sql: str
) -> QueryResult:
    """Run one already-registered, not-yet-`done` work item to completion
    and record its outcome. The caller (a scenario module) owns deciding
    *which* work items still need running (`pending_query_work_items`) and
    *when* to call this — one at a time for `sequential`/`cold_start`, from
    several threads at once for `concurrent` (each work item's row is
    independent, so concurrent calls are safe as long as `client` itself
    is only ever used from one of them — see clients/base.py's docstring
    on one connection per worker thread).
    """
    _mark(ctx, item_id, "mark_running")
    result = client.run_statement(sql, timeout_s=ctx.query_timeout_s)
    _record_result(ctx, item_id, result)
    _mark(ctx, item_id, "mark_failed" if result.error else "mark_done")
    return result


def record_connection_failure(ctx: RunContext, item_id: str, error: str) -> QueryResult:
    """Record a work item as failed because its `EngineClient` never
    connected — a different failure mode than a statement error (there was
    no statement to run), but the same "this item failed, everything else
    keeps going" contract as `run_query_work_item`. For scenarios where
    several independent connection attempts happen at once (`concurrent`),
    where one failing must not crash the others — a connect() failure
    there previously propagated out of the worker thread uncaught, which
    `ThreadPoolExecutor`/`future.result()` then re-raised and crashed the
    entire round, leaving every other item `pending` rather than one item
    correctly recorded `failed`.
    """
    _mark(ctx, item_id, "mark_running")
    result = QueryResult(error=error)
    _record_result(ctx, item_id, result)
    _mark(ctx, item_id, "mark_failed")
    return result


def _lock(ctx: RunContext):
    return ctx.ledger_lock if ctx.ledger_lock is not None else nullcontext()


def _mark(ctx: RunContext, item_id: str, method_name: str) -> None:
    with _lock(ctx):
        row = getattr(ctx.ledger, method_name)(item_id)
        ctx.wal.write("work_items", row)


def _record_result(ctx: RunContext, item_id: str, result: QueryResult) -> None:
    fields: dict[str, Any] = {name: getattr(result, name) for name in _RESULT_FIELDS}
    with _lock(ctx):
        ctx.wal.write("query_results", {"work_item_id": item_id, **fields})
        ctx.ledger.record_query_result(work_item_id=item_id, **fields)
