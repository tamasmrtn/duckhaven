"""Loads a generated TPC-H corpus into DuckHaven.

There is no DuckHaven bulk-load API — the documented path (and the one
`dlt-duckhaven` uses for exactly this) is the session's presigned staging
mechanism: `Connection.stage_files` returns a PUT url to upload to and a
GET url the agent reads back over httpfs, so `CREATE TABLE ... AS SELECT
... FROM read_parquet(get_url)` runs entirely through SQL the session
already knows how to execute. No storage credentials cross the client.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import httpx

from tpch_bench.datagen.tpchgen_runner import TABLES

if TYPE_CHECKING:
    from duckhaven_sql_connector import Connection

_UPLOAD_TIMEOUT_S = 300.0


@dataclass(frozen=True)
class LoadResult:
    table: str
    row_count: int
    load_duration_ms: float


def _stage_and_upload(conn: Connection, local_path: Path) -> str:
    staged = conn.stage_files([local_path.name])
    file = staged.files[0]
    with local_path.open("rb") as body:
        response = httpx.put(file.put_url, content=body, timeout=_UPLOAD_TIMEOUT_S)
    response.raise_for_status()
    return file.get_url


def load_table(conn: Connection, *, table: str, local_path: Path) -> LoadResult:
    """CREATE TABLE `table` from a single unpartitioned Parquet file. Fails
    (raising whatever the connector raises) if `table` already exists —
    callers that need idempotent reloads should DROP TABLE first."""
    start = time.monotonic()
    get_url = _stage_and_upload(conn, local_path)
    cursor = conn.cursor()
    cursor.execute(f"CREATE TABLE {table} AS SELECT * FROM read_parquet('{get_url}')")
    cursor.execute(f"SELECT count(*) FROM {table}")
    row_count = cursor.fetchone()[0]
    return LoadResult(
        table=table, row_count=row_count, load_duration_ms=(time.monotonic() - start) * 1000
    )


def load_corpus(
    conn: Connection, corpus_dir: Path, *, tables: tuple[str, ...] = TABLES
) -> list[LoadResult]:
    """Load every unpartitioned `<table>.parquet` under `corpus_dir` (as
    `datagen.generate()` lays it out for `parts=None`) into a table of the
    same name."""
    results = []
    for table in tables:
        local_path = corpus_dir / f"{table}.parquet"
        results.append(load_table(conn, table=table, local_path=local_path))
    return results
