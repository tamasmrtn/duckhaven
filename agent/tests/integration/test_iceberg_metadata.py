"""Agent Iceberg-native metadata probe against a live Polaris + object store.

Opt-in (`-m integration`); requires a live Polaris on object storage (see
conftest; `make polaris-dev` provides a local MinIO-backed stack). This is the
verification gate for the table-detail metadata feature: it confirms that the
version-sensitive `iceberg_snapshots` / `iceberg_metadata` calls in
`runner._iceberg_metadata` actually resolve against the agent's DuckDB `iceberg`
extension over an attached REST catalog.
"""

from __future__ import annotations

import duckdb
import pytest

from agent.executor import runner

pytestmark = pytest.mark.integration


def _attach(
    conn: duckdb.DuckDBPyConnection,
    base_url: str,
    warehouse: str,
    ns: str,
    creds,
) -> None:
    runner._attach_catalogs(
        conn,
        catalogs=[
            {
                "slug": warehouse,
                "polaris_name": warehouse,
                "backend": {"kind": "s3"},
                "default_schema": ns,
            }
        ],
        active_catalog=warehouse,
        polaris={
            "endpoint": base_url,
            "client_id": creds[0],
            "client_secret": creds[1],
        },
    )


async def test_iceberg_metadata_on_written_table(
    polaris_base_url: str, polaris_creds, polaris_s3_catalog: tuple[str, str]
) -> None:
    """After a write, the probe reports a snapshot id, a data-file count, and
    no row-level deletes for a table written by plain INSERTs."""
    catalog, ns = polaris_s3_catalog
    conn = duckdb.connect()
    try:
        conn.execute("INSTALL httpfs")
        conn.execute("LOAD httpfs")
        _attach(conn, polaris_base_url, catalog, ns, polaris_creds)
        conn.execute("INSERT INTO events VALUES (1, 'one'), (2, 'two')")
        meta = runner._iceberg_metadata(conn, catalog, ns, "events")
    finally:
        conn.close()

    assert meta["snapshot_id"] is not None
    assert meta["snapshot_at"] is not None
    assert meta["data_file_count"] is not None and meta["data_file_count"] >= 1
    assert meta["has_deletes"] is False
