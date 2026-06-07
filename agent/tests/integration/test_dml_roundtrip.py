"""DML roundtrips against a live Polaris + MinIO Iceberg table.

Beyond the existing CREATE/INSERT/SELECT smoke (`test_create_table.py`), this
covers the mutation surface: UPDATE/DELETE and multi-statement bodies, writing
real Iceberg snapshots to MinIO via vended credentials.

DuckDB's Iceberg write support is evolving and row-level UPDATE/DELETE against a
REST catalog may be unavailable in a given DuckDB build. Such cases are
*skipped* (capability gap), not failed — only an explicit unsupported/not-
implemented error qualifies, so genuine regressions still surface.
"""

from __future__ import annotations

import duckdb
import pytest

pytestmark = pytest.mark.integration

# Substrings DuckDB/its Iceberg extension use when an operation isn't available.
_UNSUPPORTED = ("not support", "not implemented", "unsupported", "no support")


def _run_or_skip(conn: duckdb.DuckDBPyConnection, sql: str) -> None:
    """Execute a mutation, skipping the test when the engine reports the
    operation is unavailable (a capability gap, not a defect)."""
    try:
        conn.execute(sql)
    except duckdb.Error as exc:
        if any(s in str(exc).lower() for s in _UNSUPPORTED):
            pytest.skip(f"DuckDB Iceberg build lacks support: {exc}")
        raise


async def test_update_then_select(polaris_s3_catalog, attach_factory) -> None:
    catalog, ns = polaris_s3_catalog
    conn = attach_factory(catalog, ns)
    conn.execute("INSERT INTO events VALUES (1, 'one'), (2, 'two'), (3, 'three')")
    _run_or_skip(conn, "UPDATE events SET label = 'TWO' WHERE id = 2")
    rows = conn.execute("SELECT id, label FROM events ORDER BY id").fetchall()
    assert rows == [(1, "one"), (2, "TWO"), (3, "three")]


async def test_delete_then_select(polaris_s3_catalog, attach_factory) -> None:
    catalog, ns = polaris_s3_catalog
    conn = attach_factory(catalog, ns)
    conn.execute("INSERT INTO events VALUES (1, 'one'), (2, 'two'), (3, 'three')")
    _run_or_skip(conn, "DELETE FROM events WHERE id = 2")
    rows = conn.execute("SELECT id, label FROM events ORDER BY id").fetchall()
    assert rows == [(1, "one"), (3, "three")]


async def test_create_insert_drop_new_table(polaris_s3_catalog, attach_factory) -> None:
    """A full table lifecycle inside the attached catalog/namespace (CREATE,
    INSERT, SELECT, DROP are all supported by the Iceberg extension)."""
    catalog, ns = polaris_s3_catalog
    conn = attach_factory(catalog, ns)
    conn.execute("CREATE TABLE metrics (k VARCHAR, v BIGINT)")
    conn.execute("INSERT INTO metrics VALUES ('a', 10), ('b', 20)")
    assert conn.execute("SELECT sum(v) FROM metrics").fetchone()[0] == 30
    conn.execute("DROP TABLE metrics")
    tables = {r[0] for r in conn.execute("SHOW TABLES").fetchall()}
    assert "metrics" not in tables


async def test_multi_statement_insert_body(polaris_s3_catalog, attach_factory) -> None:
    """The agent runner executes allow-listed multi-statement bodies; the final
    SELECT's rows are what get materialised."""
    catalog, ns = polaris_s3_catalog
    conn = attach_factory(catalog, ns)
    conn.execute("INSERT INTO events VALUES (10, 'ten');INSERT INTO events VALUES (11, 'eleven');")
    rows = conn.execute("SELECT id, label FROM events ORDER BY id").fetchall()
    assert rows == [(10, "ten"), (11, "eleven")]
