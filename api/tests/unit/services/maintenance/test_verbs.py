"""The exact SQL a destructive maintenance operation will run.

Pure, so it is testable without an agent or a catalog -- which is the reason
the statements are built in the control plane rather than on the agent. The
call shapes here were read from `duckdb_functions()` against the pinned
extension, not inferred: two verbs take the table positionally with a `schema`
keyword and one takes both as keywords with different names, and guessing binds
to no overload and fails at apply time.
"""

from __future__ import annotations

import pytest

from api.services.maintenance.verbs import APPLICABLE_KINDS, VERB_SCOPE, render


def _render(kind: str, **over):
    args = {
        "catalog": "lake",
        "schema": "analytics",
        "table": "events",
        "retention_days": 7,
    } | over
    return render(kind, **args)


def test_every_applicable_kind_renders_and_has_a_scope():
    """A kind that is applicable but unscoped would reach the confirm dialog
    with nothing to say about what it touches."""
    for kind in APPLICABLE_KINDS:
        assert _render(kind), kind
        assert VERB_SCOPE[kind] in ("table", "catalog")


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        (
            "compact_small_files",
            "CALL ducklake_merge_adjacent_files('lake', 'events', schema => 'analytics')",
        ),
        (
            "rewrite_data_files",
            "CALL ducklake_rewrite_data_files('lake', 'events', schema => 'analytics')",
        ),
        (
            "flush_inlined_data",
            "CALL ducklake_flush_inlined_data('lake', table_name => 'events', "
            "schema_name => 'analytics')",
        ),
        (
            "expire_snapshots",
            "CALL ducklake_expire_snapshots('lake', older_than => now() - INTERVAL '7 days')",
        ),
        (
            "cleanup_orphans",
            "CALL ducklake_cleanup_old_files('lake', older_than => now() - INTERVAL '7 days')",
        ),
    ],
)
def test_the_rendered_statement_is_exactly_this(kind, expected):
    assert _render(kind) == [expected]


def test_cleanup_is_always_bounded_by_retention():
    """`cleanup_all => true` deletes every file no live snapshot references --
    including ones a time-travel reader is still entitled to. The difference
    between reclaiming space and deleting someone's history."""
    for kind in APPLICABLE_KINDS:
        for statement in _render(kind):
            assert "cleanup_all" not in statement


def test_a_quote_in_an_identifier_cannot_escape_the_literal():
    """Slugs are validated upstream, but these are values, so they are quoted
    as values rather than trusted."""
    rendered = _render("compact_small_files", table="ev'ents")[0]
    assert "'ev''ents'" in rendered


def test_an_unknown_kind_refuses_rather_than_rendering_nothing():
    """Returning [] would make Apply silently succeed having done nothing."""
    with pytest.raises(ValueError, match="No maintenance verb"):
        _render("investigate_growth")


def test_catalog_scoped_verbs_name_no_table():
    """They act on every table, so including one would misrepresent the blast
    radius in the statement the user is shown."""
    for kind in ("expire_snapshots", "cleanup_orphans"):
        assert "events" not in _render(kind)[0]
        assert VERB_SCOPE[kind] == "catalog"
