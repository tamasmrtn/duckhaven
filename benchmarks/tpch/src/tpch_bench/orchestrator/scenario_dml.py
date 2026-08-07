"""DML scenario (plan §4, config/scenarios.yaml): DELETE + INSERT on ~1% of
rows against the tables the write scenario already created, simulating an
incremental refresh — `cycles` rounds, each targeting a disjoint ~1% slice
via `<key> % 100 = <cycle>` so repeated cycles don't just churn the same
rows.

Targets `tpch_write_<shape>_r0` (`scenario_write.py`'s first-rep table for
each shape) rather than every write rep's table — DML measures the cost of
refreshing an already-materialized table, not something multiplied across
however many write reps happened to run.

Each cycle is two work items, `<shape>_delete` and `<shape>_insert`
(`rep=cycle`), not one: DELETE and INSERT have genuinely different costs,
and collapsing them into a single recorded result would throw that away.
The INSERT's SELECT body is read straight out of the same
`ddl/<engine>/<shape>.sql` CTAS `scenario_write.py` used, with the same
row filter appended, sourced fresh from the base tables every cycle (not
from the write-target table itself) so cycles never run out of rows to
reinsert.

DuckHaven's Iceberg maintenance is read-only/advisory only — repeated
DELETE+INSERT here accumulates small files/snapshots with no in-app
remediation (config/scenarios.yaml's `duckhaven_no_compaction_caveat`).
Disclosed in METHODOLOGY.md, not worked around.
"""

from __future__ import annotations

from tpch_bench.clients.base import EngineClient
from tpch_bench.orchestrator.runner import (
    RunContext,
    pending_query_work_items,
    query_work_item_id,
    register_query_work_items,
    run_query_work_item,
)
from tpch_bench.orchestrator.scenario_write import target_table_name

SCENARIO = "dml"

# The column each shape's *materialized* table filters "~1% of rows" on
# for DELETE. Always unqualified: a CTAS's output columns are never
# qualified by their source table/alias regardless of how the SELECT list
# referenced them (`l.l_orderkey` in wide.sql's SELECT still materializes
# as a plain `l_orderkey` column) — so both shapes use the same name here,
# and there is exactly one table in a bare `DELETE FROM` for a qualifier
# to (wrongly) refer to anyway.
_DELETE_FILTER_COLUMN = "l_orderkey"

# The same logical column, but qualified exactly as it appears in each
# shape's own SELECT body — needed for the INSERT, which re-runs that body
# against the base tables (narrow has no table alias; wide aliases
# lineitem as `l`). See ddl/<engine>/{narrow,wide}.sql.
_INSERT_FILTER_COLUMN = {"narrow": "l_orderkey", "wide": "l.l_orderkey"}


def _select_body(ctas_sql: str) -> str:
    """The `SELECT ...` body of a `CREATE TABLE ... AS SELECT ...`
    statement (`ddl/<engine>/<shape>.sql`), reused for the INSERT so its
    row shape can never drift from what the CTAS actually created."""
    return ctas_sql[ctas_sql.index("SELECT") :].rstrip().rstrip(";")


def _delete_sql(shape: str, cycle: int) -> str:
    table = target_table_name(shape, rep=0)
    return f"DELETE FROM {table} WHERE {_DELETE_FILTER_COLUMN} % 100 = {cycle}"


def _insert_sql(shape: str, ctas_sql: str, cycle: int) -> str:
    table = target_table_name(shape, rep=0)
    column = _INSERT_FILTER_COLUMN[shape]
    return f"INSERT INTO {table}\n{_select_body(ctas_sql)}\nWHERE {column} % 100 = {cycle}"


def run(
    ctx: RunContext,
    client: EngineClient,
    ddl_by_shape: dict[str, str],
    *,
    cycles: int,
) -> None:
    """`ddl_by_shape`: {shape: sql} — the same `ddl/<engine>/<shape>.sql`
    contents given to `scenario_write.run`; requires that scenario's `_r0`
    table for each shape to already exist."""
    query_ids = [f"{shape}_delete" for shape in ddl_by_shape] + [
        f"{shape}_insert" for shape in ddl_by_shape
    ]
    items = register_query_work_items(ctx, scenario=SCENARIO, query_ids=query_ids, reps=cycles)
    pending = pending_query_work_items(ctx, items)
    if not pending:
        return
    client.connect()
    # Group by cycle so a shape's delete always runs immediately before its
    # own insert, never interleaved with another shape's or cycle's.
    for cycle in range(cycles):
        for shape, ctas_sql in ddl_by_shape.items():
            delete_id = query_work_item_id(
                ctx, scenario=SCENARIO, query_id=f"{shape}_delete", rep=cycle
            )
            delete_failed = ctx.ledger.status(delete_id) == "failed"
            if delete_id in pending:
                result = run_query_work_item(
                    ctx, client, item_id=delete_id, sql=_delete_sql(shape, cycle)
                )
                delete_failed = result.error is not None

            insert_id = query_work_item_id(
                ctx, scenario=SCENARIO, query_id=f"{shape}_insert", rep=cycle
            )
            # A failed delete must not be followed by its insert: the rows
            # it should have removed are still there, so inserting the same
            # ~1% again would duplicate them instead of refreshing them.
            # Leaving the insert `pending` is correct — a retried delete on
            # the next run makes it eligible again.
            if insert_id in pending and not delete_failed:
                run_query_work_item(
                    ctx, client, item_id=insert_id, sql=_insert_sql(shape, ctas_sql, cycle)
                )
