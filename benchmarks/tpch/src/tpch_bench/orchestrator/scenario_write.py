"""Write scenario (plan §4, config/scenarios.yaml): a CTAS-equivalent
materialization for each table-shape variant in `ddl/<engine>/{narrow,wide}.sql`
— narrow (a single table's own columns) and wide (a denormalized,
multi-table join).

A `CREATE TABLE ... AS SELECT` can only succeed once against a given table
name — unlike the read scenarios' queries, reps aren't naturally
re-runnable. So each rep targets its own table, `tpch_write_<shape>_r<rep>`
(`target_table_name` renames the DDL's `CREATE TABLE` target before it
runs), rather than requiring a `DROP TABLE` between reps that would erase
the previous rep's output and couple reps together.

DuckHaven-specific: `duckhaven_pre_statement` (config/scenarios.yaml,
`"SET duckhaven_concurrency = 'single'"`) is issued once per work item
before the CTAS. It is not itself a work item — it has no independent
timing worth recording, only the CTAS that follows it does.

**Correction, verified against the real agent source during Phase 0
(`agent/src/agent/control/channel.py::_build_request`,
`agent/src/agent/executor/admission.py`,
`shared/src/duckhaven_shared/concurrency.py`) — see
METHODOLOGY.md §9 for the frozen-doc erratum**: `SET duckhaven_concurrency`
changes how many queries share the agent's admission budget concurrently
(`single` = one at a time), not the fallback bucket size an *unestimable*
statement (any CTAS/INSERT/DELETE — `estimate_memory_bytes` only handles a
bare SELECT) is pinned to. That fraction (`estimate_fallback_bucket`,
default `"M"` = ⅓) is a fixed fraction of the agent's *total* budget
regardless of concurrency profile, so this pre-statement does not, by
itself, give a write more memory — plan §2 gotcha 2 and this scenario's
prior docstring both overstated what it does. The real levers are
provisioning a larger agent for a write-heavy run, or raising
`ESTIMATE_FALLBACK_BUCKET` on the agent itself; neither is wired up by
this harness yet.
"""

from __future__ import annotations

from tpch_bench.clients.base import EngineClient
from tpch_bench.orchestrator.runner import (
    RunContext,
    pending_query_work_items,
    register_query_work_items,
    run_query_work_item,
)

SCENARIO = "write"


def target_table_name(shape: str, rep: int) -> str:
    return f"tpch_write_{shape}_r{rep}"


def _rename_ctas_target(sql: str, shape: str, rep: int) -> str:
    base_name = f"tpch_write_{shape}"
    return sql.replace(base_name, target_table_name(shape, rep), 1)


def run(
    ctx: RunContext,
    client: EngineClient,
    ddl_by_shape: dict[str, str],
    *,
    reps: int,
    duckhaven_pre_statement: str | None = None,
) -> None:
    """`ddl_by_shape`: {shape: sql}, e.g. {"narrow": "...", "wide": "..."}
    — the contents of `ddl/<engine>/<shape>.sql`, each a `CREATE TABLE
    tpch_write_<shape> AS SELECT ...` statement. `duckhaven_pre_statement`
    is only meaningful (and should only be passed) for `ctx.engine ==
    "duckhaven"`; other engines have no equivalent memory-budget knob.
    """
    items = register_query_work_items(
        ctx, scenario=SCENARIO, query_ids=list(ddl_by_shape), reps=reps
    )
    pending = pending_query_work_items(ctx, items)
    if not pending:
        return
    client.connect()
    for item_id, (shape, rep) in pending.items():
        if duckhaven_pre_statement is not None:
            client.run_statement(duckhaven_pre_statement, timeout_s=ctx.query_timeout_s)
        sql = _rename_ctas_target(ddl_by_shape[shape], shape, rep)
        run_query_work_item(ctx, client, item_id=item_id, sql=sql)
