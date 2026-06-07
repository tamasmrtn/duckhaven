"""Data-integrity edge cases through the real Iceberg write/read path.

These guard against silent corruption in the agent's DuckDB ↔ Polaris ↔ MinIO
path: unicode, NULLs, quoting, numeric extremes, and large result sets must all
round-trip byte-exact through real Parquet/Iceberg storage.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

pytestmark = pytest.mark.integration


async def test_unicode_and_quoting_roundtrip(polaris_s3_catalog, attach_factory) -> None:
    catalog, ns = polaris_s3_catalog
    conn = attach_factory(catalog, ns)
    labels = ["héllo", "日本語", "emoji 🦆", "tab\tsep", "quote ' double \" mix", ""]
    conn.executemany("INSERT INTO events VALUES (?, ?)", [(i, s) for i, s in enumerate(labels)])
    rows = conn.execute("SELECT id, label FROM events ORDER BY id").fetchall()
    assert [s for _, s in rows] == labels


async def test_null_handling(polaris_s3_catalog, attach_factory) -> None:
    catalog, ns = polaris_s3_catalog
    conn = attach_factory(catalog, ns)
    conn.execute("INSERT INTO events VALUES (1, NULL), (NULL, 'orphan'), (NULL, NULL)")
    rows = conn.execute("SELECT id, label FROM events ORDER BY id NULLS LAST").fetchall()
    assert (1, None) in rows
    assert (None, "orphan") in rows
    assert (None, None) in rows
    assert conn.execute("SELECT count(*) FROM events WHERE label IS NULL").fetchone()[0] == 2


async def test_numeric_edges(polaris_s3_catalog, attach_factory) -> None:
    catalog, ns = polaris_s3_catalog
    conn = attach_factory(catalog, ns)
    conn.execute("CREATE TABLE nums (i BIGINT, d DOUBLE, dec DECIMAL(38,9))")
    big = 9223372036854775807  # int64 max
    conn.execute(
        "INSERT INTO nums VALUES (?, ?, ?), (?, ?, ?)",
        [big, 1.5e308, Decimal("12345.123456789"), -big - 1, -1.5e308, Decimal("-0.000000001")],
    )
    rows = conn.execute("SELECT i, d, dec FROM nums ORDER BY i").fetchall()
    assert rows[0][0] == -big - 1
    assert rows[1][0] == big
    assert rows[1][2] == Decimal("12345.123456789")


async def test_large_result_set(polaris_s3_catalog, attach_factory) -> None:
    """A wider-than-a-batch result materialises and reads back fully."""
    catalog, ns = polaris_s3_catalog
    conn = attach_factory(catalog, ns)
    conn.execute("INSERT INTO events SELECT i, 'row-' || i FROM range(50000) t(i)")
    count = conn.execute("SELECT count(*) FROM events").fetchone()[0]
    assert count == 50000
    # Spot-check ordering/content at the tail.
    last = conn.execute("SELECT id, label FROM events ORDER BY id DESC LIMIT 1").fetchone()
    assert last == (49999, "row-49999")
