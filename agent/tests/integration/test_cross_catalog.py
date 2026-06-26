"""Cross-catalog join over two real Polaris catalogs (multi-attach).

Provisions two S3-backed catalogs, multi-attaches both under their own aliases
(as the agent runner does), and joins `raw.analytics.events` to
`curated.analytics.events` — proving that the eager multi-attach path resolves
fully-qualified `catalog.schema.table` references across catalogs.

Polaris is object-storage only; requires POLARIS_BASE_URL + POLARIS_S3_BUCKET
(`make polaris-dev` provides a local MinIO-backed stack). Skipped otherwise.
"""

from __future__ import annotations

import os
from contextlib import AsyncExitStack

import duckdb
import pytest
from testkit import polaris as dh_polaris
from testkit.iceberg import attach_catalogs

pytestmark = pytest.mark.integration


async def test_cross_catalog_join(polaris_base_url: str, polaris_creds: tuple[str, str]) -> None:
    if not os.getenv("POLARIS_S3_BUCKET"):
        pytest.skip("POLARIS_S3_BUCKET not set; skipping cross-catalog integration test")

    async with AsyncExitStack() as stack:
        raw_name, ns = await stack.enter_async_context(
            dh_polaris.s3_catalog(polaris_base_url, polaris_creds, prefix="dh_xc_raw")
        )
        cur_name, _ = await stack.enter_async_context(
            dh_polaris.s3_catalog(polaris_base_url, polaris_creds, prefix="dh_xc_cur")
        )

        conn = duckdb.connect()
        try:
            # Attach both catalogs under domain-style aliases; `raw` is active.
            attach_catalogs(
                conn,
                polaris_base_url,
                [("raw", raw_name), ("curated", cur_name)],
                active="raw",
                namespace=ns,
                creds=polaris_creds,
            )
            # The seed table is analytics.events(id long, label string) in each.
            conn.execute('INSERT INTO "raw"."analytics"."events" VALUES (1, \'a\')')
            conn.execute('INSERT INTO "curated"."analytics"."events" VALUES (1, \'b\')')

            row = conn.execute(
                "SELECT r.id, r.label, c.label "
                'FROM "raw"."analytics"."events" r '
                'JOIN "curated"."analytics"."events" c ON r.id = c.id'
            ).fetchone()
            assert row == (1, "a", "b")

            # Unqualified names resolve against the active catalog (`raw`).
            unq = conn.execute('SELECT label FROM "analytics"."events"').fetchone()
            assert unq == ("a",)
        finally:
            conn.close()
