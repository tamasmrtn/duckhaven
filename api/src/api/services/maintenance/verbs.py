"""The maintenance statements DuckHaven runs, rendered but not executed.

A pure module: no database, no agent, no I/O. That is what makes the exact SQL
a destructive operation will run unit-testable, which matters more here than
anywhere else in the maintenance subsystem.

Only DuckLake has verbs to render. DuckDB's `ducklake` extension implements
compaction, snapshot expiry and file cleanup; its `iceberg` extension
implements none of them, which is why an Iceberg recommendation names an
external engine instead and never reaches this module.

The statements are written here rather than in the agent so the control plane
owns what runs, and so `recommend.py`'s displayed command and the applied one
come from the same place.
"""

from __future__ import annotations

from typing import Literal

# Whether a verb acts on one table or on the whole catalog. This drives the
# confirmation the user sees: `expire_snapshots` on one table's page still
# expires every table's snapshots, and saying so is not optional.
VERB_SCOPE: dict[str, Literal["table", "catalog"]] = {
    "compact_small_files": "table",
    "rewrite_data_files": "table",
    "flush_inlined_data": "table",
    "expire_snapshots": "catalog",
    "cleanup_orphans": "catalog",
}

# The recommendation kinds DuckHaven can act on. `investigate_growth` is absent
# deliberately: it prescribes nothing, so there is nothing to run.
APPLICABLE_KINDS = frozenset(VERB_SCOPE)

# Each verb's call shape, taken from duckdb_functions() rather than assumed.
# They genuinely differ: two take the table positionally with a `schema`
# keyword, and flush_inlined_data takes both as keywords with different names.
# Guessing one from the other binds to no overload and fails at apply time.
_TABLE_VERBS: dict[str, str] = {
    "compact_small_files": "ducklake_merge_adjacent_files({cat}, {tbl}, schema => {sch})",
    "rewrite_data_files": "ducklake_rewrite_data_files({cat}, {tbl}, schema => {sch})",
    "flush_inlined_data": (
        "ducklake_flush_inlined_data({cat}, table_name => {tbl}, schema_name => {sch})"
    ),
}


def _literal(value: str) -> str:
    """A SQL string literal. Slugs and identifiers are validated upstream, but
    these are values rather than identifiers, so they are quoted as such."""
    return "'" + value.replace("'", "''") + "'"


def render(
    kind: str,
    *,
    catalog: str,
    schema: str,
    table: str,
    retention_days: int,
) -> list[str]:
    """The statements that apply ``kind``, in order.

    Never emits ``cleanup_all => true``. That deletes every file no live
    snapshot references, including ones a time-travel reader is still entitled
    to; bounding cleanup by the policy's retention is the difference between
    reclaiming space and deleting someone's history.
    """
    if kind not in APPLICABLE_KINDS:
        raise ValueError(f"No maintenance verb for recommendation kind {kind!r}")

    if template := _TABLE_VERBS.get(kind):
        call = template.format(cat=_literal(catalog), tbl=_literal(table), sch=_literal(schema))
        return [f"CALL {call}"]

    older_than = f"now() - INTERVAL '{int(retention_days)} days'"
    if kind == "expire_snapshots":
        return [f"CALL ducklake_expire_snapshots({_literal(catalog)}, older_than => {older_than})"]
    return [f"CALL ducklake_cleanup_old_files({_literal(catalog)}, older_than => {older_than})"]
