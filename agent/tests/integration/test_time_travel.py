"""Agent Iceberg time-travel ("query at this snapshot") against live Polaris.

Opt-in (`-m integration`); requires a live Polaris on object storage (see
conftest; `make polaris-dev` provides a local MinIO-backed stack). This is the
verification gate for the snapshot-history "Query at this snapshot" feature: it
confirms DuckDB's `AT (VERSION => …)` / `AT (TIMESTAMP => …)` clause resolves a
*past* table state over the agent's attached REST catalog — the same path a
user worksheet takes. No `runner.py` change is needed; this locks the syntax.
"""

from __future__ import annotations

from datetime import UTC, datetime

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


async def test_time_travel_returns_past_state(
    polaris_base_url: str, polaris_creds, polaris_s3_catalog: tuple[str, str]
) -> None:
    """Two writes make two snapshots. `AT (VERSION => <older>)` and
    `AT (TIMESTAMP => <older_ts>)` both see only the first write, while the
    live table sees both."""
    catalog, ns = polaris_s3_catalog
    conn = duckdb.connect()
    try:
        conn.execute("INSTALL httpfs")
        conn.execute("LOAD httpfs")
        _attach(conn, polaris_base_url, catalog, ns, polaris_creds)

        # Snapshot A: one row.
        conn.execute("INSERT INTO events VALUES (1, 'one')")
        # iceberg_snapshots needs the catalog-qualified table: a bare name makes
        # it glob the filesystem (version guessing) instead of resolving via the
        # attached REST catalog — the same form runner._iceberg_metadata uses.
        ident = f'"{catalog}"."{ns}"."events"'
        snap_a, ts_a = conn.execute(
            f"SELECT snapshot_id, timestamp_ms FROM iceberg_snapshots({ident}) "
            "ORDER BY sequence_number DESC LIMIT 1"
        ).fetchone()

        # `iceberg_snapshots` reports the commit time as a datetime here (older
        # extensions return epoch millis); normalize to UTC epoch ms either way.
        if isinstance(ts_a, datetime):
            dt = ts_a if ts_a.tzinfo else ts_a.replace(tzinfo=UTC)
            ts_a_ms = int(dt.timestamp() * 1000)
        else:
            ts_a_ms = int(ts_a)

        # Snapshot B: a second row.
        conn.execute("INSERT INTO events VALUES (2, 'two')")

        live = conn.execute("SELECT count(*) FROM events").fetchone()[0]
        as_of_version = conn.execute(
            f"SELECT count(*) FROM events AT (VERSION => {snap_a})"
        ).fetchone()[0]
        as_of_time = conn.execute(
            f"SELECT count(*) FROM events AT (TIMESTAMP => epoch_ms({ts_a_ms}))"
        ).fetchone()[0]
    finally:
        conn.close()

    assert live == 2
    assert as_of_version == 1
    assert as_of_time == 1
