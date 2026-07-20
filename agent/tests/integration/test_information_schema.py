"""Agent `information_schema` metadata surface against a live Polaris catalog.

Opt-in (`-m integration`); requires a live Polaris on object storage (see
conftest; `make polaris-dev` provides a local MinIO-backed stack). This is the
verification gate for the built-in read-only `information_schema` feature: it
confirms what the live collision check could not (the dev catalog was empty) —
that DuckDB's native, global `information_schema` views *populate* for an
attached Polaris/Iceberg catalog, so the feature can lean on them rather than
materialising anything in Polaris.

It pins the facts the design depends on, including a DuckDB limitation:
1. `information_schema.tables` (unqualified -> DuckDB's global view) lists
   objects from the attached catalog, scoped with `table_catalog`.
2. `information_schema.columns` does NOT yet introspect Iceberg columns in
   DuckDB 1.5.x (it yields a placeholder), so the supported column-detail path
   is `DESCRIBE`. The test asserts both, and the columns-view check is a canary:
   when a future DuckDB starts populating it, this test fails so we update docs.
3. `<catalog>.information_schema` is not a per-catalog schema inside an attached
   Iceberg catalog, so qualifying with the alias fails to resolve a table there.

`test_relation_metadata_matrix` pins the full surface — which statements DuckHaven
can rely on for relation metadata and which are broken — against a single held
connection, because whether a metadata view has been *hydrated* depends on what
else that connection touched (see the docstring there).
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import duckdb
import pytest

from agent.executor.runner import run_query_sync

pytestmark = pytest.mark.integration


def _run(
    sql: str,
    *,
    base_url: str,
    creds: tuple[str, str],
    catalog: str,
    ns: str,
    tmp_path: Path,
) -> tuple[dict, Path]:
    """Execute one statement through the real runner with the catalog attached.
    Returns the runner result and the result-Parquet path (for SELECTs)."""
    out = tmp_path / f"{uuid4().hex}.parquet"
    result = run_query_sync(
        sql,
        out,
        memory_bytes=1024**3,
        threads=2,
        catalogs=[
            {
                "slug": catalog,
                "polaris_name": catalog,
                "backend": {"kind": "s3"},
                "default_schema": ns,
            }
        ],
        active_catalog=catalog,
        polaris={"endpoint": base_url, "client_id": creds[0], "client_secret": creds[1]},
    )
    return result, out


async def test_information_schema_lists_attached_catalog_objects(
    polaris_base_url: str, polaris_creds, polaris_s3_catalog: tuple[str, str], tmp_path: Path
) -> None:
    catalog, ns = polaris_s3_catalog
    table = f"dh_info_{uuid4().hex[:8]}"

    def run(sql: str) -> tuple[dict, Path]:
        return _run(
            sql,
            base_url=polaris_base_url,
            creds=polaris_creds,
            catalog=catalog,
            ns=ns,
            tmp_path=tmp_path,
        )

    run(f"CREATE TABLE {table} (id INTEGER, label VARCHAR)")
    try:
        # information_schema.tables lists the new table for the attached catalog.
        result, out = run(
            "SELECT table_type FROM information_schema.tables "
            f"WHERE table_catalog = '{catalog}' AND table_schema = '{ns}' "
            f"AND table_name = '{table}'"
        )
        assert result["wrote_result"] is True
        rows = duckdb.read_parquet(str(out)).fetchall()
        assert rows == [("BASE TABLE",)]

        # Column detail: DESCRIBE (a SELECT to DuckDB) returns the real columns
        # and types. This is the documented column-introspection path.
        _, out = run(f"SELECT column_name, column_type FROM (DESCRIBE {table})")
        cols = duckdb.read_parquet(str(out)).fetchall()
        assert cols == [("id", "INTEGER"), ("label", "VARCHAR")]

        # Canary: information_schema.columns cannot yet introspect Iceberg
        # columns in DuckDB 1.5.x (it returns a single UNKNOWN placeholder), so
        # the real column names are absent. If a future DuckDB fixes this, the
        # assertion fails and the docs admonition should be removed.
        _, out = run(
            "SELECT column_name FROM information_schema.columns "
            f"WHERE table_catalog = '{catalog}' AND table_schema = '{ns}' "
            f"AND table_name = '{table}'"
        )
        names = {r[0] for r in duckdb.read_parquet(str(out)).fetchall()}
        assert not {"id", "label"}.issubset(names)

        # There is no per-catalog information_schema inside an attached Iceberg
        # catalog: qualifying with the catalog alias must not resolve a table
        # there. This is the collision-avoidance guarantee the design relies on.
        with pytest.raises(duckdb.Error):
            run(f"SELECT * FROM {catalog}.information_schema.tables")
    finally:
        run(f"DROP TABLE {table}")


async def test_relation_metadata_matrix(
    polaris_s3_catalog: tuple[str, str], attach_factory
) -> None:
    """The full relation-metadata surface for an attached Iceberg catalog.

    Runs on **one held connection** (the `attach_factory` fixture attaches under
    the alias `dh_catalog`) rather than through `run_query_sync`, because the
    single most surprising fact here is order-dependent: DuckDB loads an Iceberg
    table's schema *lazily*, per table, on first touch. `information_schema.columns`
    circumvents that (duckdb/duckdb-iceberg#1146), so it reports a `('__','UNKNOWN')`
    placeholder for every untouched table — and real columns for tables the same
    connection has already described or scanned. Both halves are asserted below.

    This distinction is exactly the single-shot/session split in DuckHaven: the
    `/queries` path opens a fresh connection per statement, so the view is
    *always* placeholders there; a SQL session holds its connection, so the view
    is *inconsistent* — correct for whatever that session happened to touch. In
    neither case can a client trust it, which is why `DESCRIBE` is the contract.

    The placeholder assertions are canaries: if a future DuckDB populates these
    views eagerly, they fail, and `docs/reference/sql-support.md` should be updated.
    """
    catalog, ns = polaris_s3_catalog
    conn = attach_factory(catalog, ns)
    alias = "dh_catalog"
    described = f"dh_desc_{uuid4().hex[:8]}"
    untouched = f"dh_untouched_{uuid4().hex[:8]}"
    # A deliberately non-trivial schema: decimal, timestamptz, list and nested
    # struct are the types a client is most likely to get wrong.
    conn.execute(
        f'CREATE TABLE "{alias}"."{ns}"."{described}" '
        "(id BIGINT, amount DECIMAL(18,4), ts TIMESTAMPTZ, "
        "tags VARCHAR[], addr STRUCT(city VARCHAR, zip INT))"
    )
    conn.execute(f'CREATE TABLE "{alias}"."{ns}"."{untouched}" (a INTEGER)')
    expected_columns = [
        ("id", "BIGINT"),
        ("amount", "DECIMAL(18,4)"),
        ("ts", "TIMESTAMP WITH TIME ZONE"),
        ("tags", "VARCHAR[]"),
        ("addr", "STRUCT(city VARCHAR, zip INTEGER)"),
    ]

    def rows(sql: str) -> list[tuple]:
        return conn.execute(sql).fetchall()

    try:
        # --- Listing: works, and spans every attached catalog -----------------
        schemata = rows("SELECT catalog_name, schema_name FROM information_schema.schemata")
        assert (alias, ns) in schemata
        listed = rows(
            "SELECT table_name FROM information_schema.tables "
            f"WHERE table_catalog = '{alias}' AND table_schema = '{ns}'"
        )
        assert {described, untouched} <= {r[0] for r in listed}
        # duckdb_tables()/duckdb_schemas() are a second, equally working listing
        # door — which is why the scoped-catalog grant check has to close both.
        assert {described, untouched} <= {
            r[0]
            for r in rows(f"SELECT table_name FROM duckdb_tables() WHERE database_name = '{alias}'")
        }
        assert (alias, ns) in rows("SELECT database_name, schema_name FROM duckdb_schemas()")
        assert {described, untouched} <= {r[0] for r in rows("SHOW TABLES")}

        # --- Columns: the working paths --------------------------------------
        assert (
            rows(f'SELECT column_name, column_type FROM (DESCRIBE "{alias}"."{ns}"."{described}")')
            == expected_columns
        )
        # PRAGMA table_info is a second correct path (name/type in cols 1 and 2).
        assert [
            (r[1], r[2]) for r in rows(f"PRAGMA table_info('{alias}.{ns}.{described}')")
        ] == expected_columns

        # --- Columns: the broken paths (canaries) ----------------------------
        # `described` was hydrated by the DESCRIBE above; `untouched` was not.
        info_columns = dict(
            rows(
                "SELECT table_name, column_name FROM information_schema.columns "
                f"WHERE table_catalog = '{alias}' AND table_name = '{untouched}'"
            )
        )
        assert info_columns == {untouched: "__"}
        assert rows(
            f"SELECT column_name, data_type FROM duckdb_columns() "
            f"WHERE database_name = '{alias}' AND table_name = '{untouched}'"
        ) == [("__", "UNKNOWN")]
        # SHOW ALL TABLES advertises column_names/column_types and gets them
        # wrong for the same reason (duckdb/duckdb-iceberg#560).
        assert [(r[3], r[4]) for r in rows("SHOW ALL TABLES") if r[2] == untouched] == [
            (["__"], ["UNKNOWN"])
        ]

        # The hydration half: touching the table repairs the view for it alone.
        conn.execute(f'SELECT * FROM "{alias}"."{ns}"."{untouched}" LIMIT 0')
        assert rows(
            "SELECT column_name FROM information_schema.columns "
            f"WHERE table_catalog = '{alias}' AND table_name = '{untouched}'"
        ) == [("a",)]

        # --- Qualification ----------------------------------------------------
        # dbt-duckdb's `system.information_schema.<view>` spelling resolves.
        assert {described, untouched} <= {
            r[0] for r in rows("SELECT table_name FROM system.information_schema.tables")
        }
        # But there is no per-catalog information_schema inside the Iceberg catalog.
        with pytest.raises(duckdb.Error):
            rows(f'SELECT * FROM "{alias}".information_schema.tables')
    finally:
        for name in (described, untouched):
            conn.execute(f'DROP TABLE IF EXISTS "{alias}"."{ns}"."{name}"')
