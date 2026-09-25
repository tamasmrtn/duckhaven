"""The DuckLake maintenance statements DuckHaven runs, rendered but not executed.

Pure, so the exact destructive SQL is unit-testable. The displayed command in
`recommend.py` and the applied one both come from here.
"""

from __future__ import annotations

from typing import Literal

# Drives the confirmation: a catalog-scoped verb affects every table.
VERB_SCOPE: dict[str, Literal["table", "catalog"]] = {
    "compact_small_files": "table",
    "rewrite_data_files": "table",
    "flush_inlined_data": "table",
    "expire_snapshots": "catalog",
    "cleanup_orphans": "catalog",
}

# `investigate_growth` is absent: it prescribes nothing to run.
APPLICABLE_KINDS = frozenset(VERB_SCOPE)

# Call shapes from duckdb_functions(); they genuinely differ between verbs.
_TABLE_VERBS: dict[str, str] = {
    "compact_small_files": "ducklake_merge_adjacent_files({cat}, {tbl}, schema => {sch})",
    "rewrite_data_files": "ducklake_rewrite_data_files({cat}, {tbl}, schema => {sch})",
    "flush_inlined_data": (
        "ducklake_flush_inlined_data({cat}, table_name => {tbl}, schema_name => {sch})"
    ),
}


def _literal(value: str) -> str:
    """A SQL string literal."""
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

    Never emits ``cleanup_all => true``: cleanup is bounded by the policy's
    retention so time travel within it keeps working.
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
