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
