"""Unit tests for the scoped-grant resolution engine and SQL ref extraction."""

from __future__ import annotations

import pytest

from api.models.catalog_grant import CatalogGrant
from api.services.grants import (
    GrantDenied,
    access_tier,
    extract_table_refs,
    is_exempt_ref,
    schema_visible,
    tier_rank,
)


def _grant(tier: str, schema: str | None = None, table: str | None = None) -> CatalogGrant:
    """A detached CatalogGrant for pure-function tests (no session needed)."""
    return CatalogGrant(tier=tier, schema_name=schema, table_name=table)


# --- access_tier: granularity, hierarchy, cap -------------------------------


def test_catalog_level_grant_covers_every_node():
    grants = [_grant("reader")]  # catalog-level (both NULL)
    assert access_tier(grants, "reader", "marketing", "leads") == "reader"
    assert access_tier(grants, "reader", "finance", "ledger") == "reader"
    assert access_tier(grants, "reader", None, None) == "reader"


def test_schema_grant_covers_only_its_schema():
    grants = [_grant("reader", schema="marketing")]
    assert access_tier(grants, "reader", "marketing", "leads") == "reader"
    assert access_tier(grants, "reader", "finance", "ledger") is None


def test_schema_grant_covers_future_table():
    """A schema-level grant covers a table created after the grant — no
    re-granting needed (future-grants-for-free)."""
    grants = [_grant("reader", schema="marketing")]
    # A brand-new table name never mentioned in any grant still resolves.
    assert access_tier(grants, "reader", "marketing", "brand_new_table") == "reader"


def test_table_grant_is_exact():
    grants = [_grant("reader", schema="marketing", table="leads")]
    assert access_tier(grants, "reader", "marketing", "leads") == "reader"
    assert access_tier(grants, "reader", "marketing", "other") is None
    # A table grant does not authorize acting on the whole schema.
    assert access_tier(grants, "reader", "marketing", None) is None


def test_highest_covering_grant_wins():
    grants = [_grant("metadata"), _grant("writer", schema="marketing")]
    assert access_tier(grants, "writer", "marketing", "leads") == "writer"
    assert access_tier(grants, "writer", "finance", "ledger") == "metadata"


def test_metadata_tier_resolves():
    grants = [_grant("metadata", schema="marketing", table="leads")]
    assert access_tier(grants, "reader", "marketing", "leads") == "metadata"


@pytest.mark.parametrize("tier", ["metadata", "reader", "writer"])
def test_each_tier_at_each_granularity(tier):
    for schema, table in [(None, None), ("s", None), ("s", "t")]:
        grants = [_grant(tier, schema=schema, table=table)]
        assert access_tier(grants, "writer", "s", "t") == tier


def test_role_cap_cannot_promote_reader():
    """A schema-level writer grant cannot promote a workspace `reader`."""
    grants = [_grant("writer", schema="marketing")]
    assert access_tier(grants, "reader", "marketing", "leads") == "reader"
    assert access_tier(grants, "writer", "marketing", "leads") == "writer"


def test_owner_role_caps_at_writer():
    grants = [_grant("writer")]
    assert access_tier(grants, "owner", "s", "t") == "writer"


def test_no_grant_no_access():
    assert access_tier([], "writer", "s", "t") is None


def test_no_membership_no_access():
    assert access_tier([_grant("writer")], None, "s", "t") is None


# --- schema_visible: list filtering -----------------------------------------


def test_schema_visible_via_catalog_grant():
    assert schema_visible([_grant("metadata")], "anything") is True


def test_schema_visible_via_table_grant_bubbles_up():
    grants = [_grant("reader", schema="marketing", table="leads")]
    assert schema_visible(grants, "marketing") is True
    assert schema_visible(grants, "finance") is False


def test_tier_rank_ordering():
    assert tier_rank("metadata") < tier_rank("reader") < tier_rank("writer")
    assert tier_rank(None) == -1


# --- extract_table_refs ------------------------------------------------------


def _tuples(sql):
    return {(r.catalog, r.schema, r.table, r.is_target) for r in extract_table_refs(sql)}


def test_extract_qualified_select():
    assert _tuples("SELECT * FROM cat.sch.tbl") == {("cat", "sch", "tbl", False)}


def test_extract_join_lists_every_source():
    refs = _tuples("SELECT * FROM sch.a x JOIN other b ON x.id=b.id")
    assert (None, "sch", "a", False) in refs
    assert (None, None, "other", False) in refs


def test_extract_excludes_cte_alias():
    refs = extract_table_refs("WITH foo AS (SELECT * FROM realtbl) SELECT * FROM foo")
    names = {r.table for r in refs}
    assert "realtbl" in names
    assert "foo" not in names


def test_extract_insert_select_target_vs_source():
    refs = {
        (r.table, r.is_target)
        for r in extract_table_refs("INSERT INTO cat.s.t SELECT * FROM cat.s.src")
    }
    assert ("t", True) in refs
    assert ("src", False) in refs


def test_extract_merge_target_and_source():
    sql = "MERGE INTO cat.s.t USING cat.s.src ON t.id=src.id WHEN MATCHED THEN DELETE"
    refs = {(r.table, r.is_target) for r in extract_table_refs(sql)}
    assert ("t", True) in refs
    assert ("src", False) in refs


def test_extract_update_with_subquery_source():
    sql = "UPDATE cat.s.t SET x=1 WHERE id IN (SELECT id FROM cat.s.other)"
    refs = {(r.table, r.is_target) for r in extract_table_refs(sql)}
    assert ("t", True) in refs
    assert ("other", False) in refs


def test_extract_drop_target():
    refs = extract_table_refs("DROP TABLE cat.s.t")
    assert len(refs) == 1 and refs[0].is_target and refs[0].table == "t"


@pytest.mark.parametrize("sql", ["TRUNCATE TABLE cat.s.t", "TRUNCATE cat.s.t"])
def test_extract_truncate_target(sql):
    # sqlglot parses TRUNCATE to its own node whose target lives in `expressions`,
    # not `this` — so it needs `writer`, like the DELETE DuckDB turns it into.
    refs = extract_table_refs(sql)
    assert len(refs) == 1 and refs[0].is_target and refs[0].table == "t"


def test_extract_alter_target():
    refs = extract_table_refs("ALTER TABLE cat.s.t ADD COLUMN c INTEGER")
    assert len(refs) == 1 and refs[0].is_target and refs[0].table == "t"


def test_extract_cross_catalog_join():
    refs = _tuples("SELECT * FROM cat_a.s.t JOIN cat_b.s.u ON t.id=u.id")
    assert ("cat_a", "s", "t", False) in refs
    assert ("cat_b", "s", "u", False) in refs


def test_extract_parse_failure_is_denied():
    with pytest.raises(GrantDenied):
        extract_table_refs("SELECT FROM WHERE ) (")


# --- exemptions --------------------------------------------------------------


def test_system_catalog_and_info_schema_exempt():
    assert is_exempt_ref("duckhaven", "info_schema") is True
    assert is_exempt_ref(None, "information_schema") is True
    assert is_exempt_ref("mycatalog", "analytics") is False
