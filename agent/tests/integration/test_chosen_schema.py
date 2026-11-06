"""A chosen schema on a real DuckLake catalog: bare names resolve in it, it is never
created, and one that cannot be used fails the query instead of leaving names in
the in-memory default.

The catalog keeps its metadata in a local file rather than Postgres, so only the
`ducklake` extension is needed; the attach itself is the one thing replaced.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

from agent.executor import runner

pytestmark = pytest.mark.integration


@pytest.fixture
def local_ducklake(tmp_path: Path, monkeypatch):
    try:
        duckdb.connect().execute("LOAD ducklake")
    except duckdb.Error as exc:
        pytest.skip(f"ducklake extension unavailable: {exc}")
    meta, data = tmp_path / "meta.ducklake", tmp_path / "data"

    def attach(conn, cat):
        conn.execute(f"ATTACH 'ducklake:{meta}' AS \"{cat['slug']}\" (DATA_PATH '{data}/')")

    seed = duckdb.connect()
    attach(seed, {"slug": "raw"})
    seed.execute("CREATE SCHEMA raw.marts")
    seed.execute("CREATE TABLE raw.marts.orders AS SELECT 7 AS n")
    seed.close()
    monkeypatch.setattr(runner, "_attach_ducklake", attach)

    def schemas() -> set[str]:
        conn = duckdb.connect()
        attach(conn, {"slug": "raw"})
        return {
            row[0]
            for row in conn.execute(
                "SELECT schema_name FROM duckdb_schemas() WHERE database_name = 'raw'"
            ).fetchall()
        }

    return schemas


def _catalog(use_schema: str) -> dict:
    return {
        "slug": "raw",
        "kind": "ducklake",
        "backend": {"kind": "object_store", "root_uri": ""},
        "default_schema": "analytics",
        "use_schema": use_schema,
    }


def test_bare_names_resolve_in_the_chosen_schema(local_ducklake):
    conn = runner.open_and_attach(catalogs=[_catalog("marts")], active_catalog="raw")
    assert conn.execute("SELECT n FROM orders").fetchall() == [(7,)]
    conn.close()
    assert local_ducklake() == {"main", "marts"}


def test_a_schema_that_does_not_exist_fails_and_is_not_created(local_ducklake):
    with pytest.raises(runner.ChosenSchemaUnusable, match="typo"):
        runner.open_and_attach(catalogs=[_catalog("typo")], active_catalog="raw")
    assert "typo" not in local_ducklake()


def test_a_chosen_catalog_that_fails_to_attach_fails_the_query(local_ducklake, monkeypatch):
    def refuse(conn, cat):
        raise duckdb.IOException("metadata store unreachable")

    monkeypatch.setattr(runner, "_attach_ducklake", refuse)
    with pytest.raises(runner.ChosenSchemaUnusable, match="could not be attached"):
        runner.open_and_attach(catalogs=[_catalog("marts")], active_catalog="raw")
