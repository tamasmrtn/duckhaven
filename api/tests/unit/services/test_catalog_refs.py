"""Which catalogs a statement's connection attaches (on-demand attach)."""

from __future__ import annotations

import pytest

from api.models.catalog import Catalog
from api.models.storage_backend import StorageBackend
from api.services.catalog_refs import (
    dispatch_catalogs,
    lists_catalog_objects,
    statement_catalogs,
)

_SLUGS = ("main_cat", "cat_b", "cat_c", "lake")


def _catalogs() -> list[Catalog]:
    return [
        Catalog(
            slug=s,
            kind="ducklake" if s == "lake" else "iceberg_polaris",
            storage_backend=StorageBackend(kind="adls_gen2" if s == "lake" else "object_store"),
        )
        for s in _SLUGS
    ]


def _named(sql: str, active: str | None = "main_cat", **kw) -> set[str]:
    return {c.slug for c in statement_catalogs(sql, _catalogs(), active, **kw)}


# Every shape the Phase 0 study found `extract_table_refs` to miss is here too:
# a word scan has no grammar to fall short of.
@pytest.mark.parametrize(
    ("sql", "expected"),
    [
        ("SELECT 1", {"main_cat"}),
        ("SELECT * FROM t", {"main_cat"}),
        ("SELECT * FROM analytics.t", {"main_cat"}),
        ("SELECT * FROM cat_b.t", {"main_cat", "cat_b"}),
        ("SELECT * FROM cat_b.analytics.t", {"main_cat", "cat_b"}),
        ("SELECT * FROM cat_b.s.x JOIN cat_c.s.y USING (k)", {"main_cat", "cat_b", "cat_c"}),
        ("SELECT (SELECT max(k) FROM cat_c.s.z)", {"main_cat", "cat_c"}),
        ("WITH y AS (SELECT * FROM cat_b.s.y) SELECT * FROM y", {"main_cat", "cat_b"}),
        ("USE cat_b", {"main_cat", "cat_b"}),
        ("USE cat_b.analytics", {"main_cat", "cat_b"}),
        ("SET search_path = 'cat_b.analytics'", {"main_cat", "cat_b"}),
        ("CREATE SCHEMA cat_b.s2", {"main_cat", "cat_b"}),
        ("EXPLAIN SELECT * FROM cat_b.s.y", {"main_cat", "cat_b"}),
        ("CALL lake.merge_adjacent_files()", {"main_cat", "lake"}),
        ("SELECT * FROM ducklake_snapshots('lake')", {"main_cat", "lake"}),
        ("SELECT * FROM iceberg_snapshots('cat_b.s.y')", {"main_cat", "cat_b"}),
        ("SELECT * FROM query('SELECT * FROM cat_c.s.y')", {"main_cat", "cat_c"}),
        ("SELECT cat_b.s.my_macro(1)", {"main_cat", "cat_b"}),
        ('SELECT * FROM "Cat_B"."s"."y"', {"main_cat", "cat_b"}),
        ("CREATE TABLE cat_b.s.x AS SELECT * FROM cat_c.s.y", {"main_cat", "cat_b", "cat_c"}),
        ("SELECT * FROM cat_b.s.x; SELECT * FROM cat_c.s.y", {"main_cat", "cat_b", "cat_c"}),
        ("DESCRIBE\n  cat_b.s.y", {"main_cat", "cat_b"}),
        ("SELECT * FROM read_parquet('s3://bucket/x.parquet')", {"main_cat"}),
    ],
)
def test_the_catalogs_a_statement_names_are_attached(sql, expected):
    assert _named(sql) == expected


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM information_schema.tables",
        "SELECT * FROM cat_b.information_schema.columns",
        "SELECT * FROM pg_catalog.pg_tables",
        "SELECT * FROM duckdb_tables()",
        "SELECT * FROM duckdb_databases()",
        "SELECT function_name FROM duckdb_functions()",
        "SHOW TABLES",
        "show all tables",
        "SHOW DATABASES",
        "SELECT 1; SHOW TABLES",
        "DESCRIBE",
        "PRAGMA show_tables",
        "PRAGMA database_list",
    ],
)
def test_a_listing_attaches_the_whole_workspace(sql):
    """Listings span every attached catalog, so they keep doing so."""
    assert lists_catalog_objects(sql)
    assert _named(sql) == set(_SLUGS)


@pytest.mark.parametrize(
    "sql", ["SELECT * FROM cat_b.s.y", "DESCRIBE cat_b.s.y", "SELECT 'show tables'"]
)
def test_ordinary_statements_are_not_listings(sql):
    assert not lists_catalog_objects(sql)


def test_a_probe_names_its_catalog_whatever_the_text_says():
    assert _named("SELECT 1", also=["lake"]) == {"main_cat", "lake"}


def test_without_an_active_catalog_only_named_ones_attach():
    assert _named("SELECT * FROM cat_b.s.y", active=None) == {"cat_b"}


class _Agent:
    def __init__(self, features: list[str]) -> None:
        self.capabilities = {"protocol_features": features}


async def test_an_agent_that_attaches_up_front_gets_every_catalog(monkeypatch):
    import api.services.catalog_refs as refs

    monkeypatch.setattr(refs, "build_catalog_attach", _fake_build)
    payload = await dispatch_catalogs(_Agent(["statement_ack"]), _catalogs(), [])

    assert [c["slug"] for c in payload["catalogs"]] == list(_SLUGS)
    assert "workspace_catalogs" not in payload


async def test_an_on_demand_agent_gets_the_named_catalogs_and_what_to_prepare(monkeypatch):
    import api.services.catalog_refs as refs

    monkeypatch.setattr(refs, "build_catalog_attach", _fake_build)
    catalogs = _catalogs()
    payload = await dispatch_catalogs(_Agent(["on_demand_attach"]), catalogs, catalogs[:1])

    assert [c["slug"] for c in payload["catalogs"]] == ["main_cat"]
    assert payload["workspace_catalogs"] == list(_SLUGS)
    assert payload["preload"] == {
        "catalog_kinds": ["ducklake", "iceberg_polaris"],
        "backend_kinds": ["adls_gen2", "object_store"],
    }


async def _fake_build(catalog: Catalog) -> dict:
    return {"slug": catalog.slug}
