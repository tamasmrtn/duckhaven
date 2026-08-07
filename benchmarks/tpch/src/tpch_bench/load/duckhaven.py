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

# SF1/SF10's largest table fit well inside 300s; SF100's lineitem alone is
# ~27 GB and SF300's is ~3x that, so this needs real headroom, not just
# what the smallest scale factors happened to need.
_UPLOAD_TIMEOUT_S = 1800.0


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


def load_table_parts(conn: Connection, *, table: str, local_paths: list[Path]) -> LoadResult:
    """Like `load_table`, but for a table generated with `parts` — needed
    when Databricks' Files API forces a table's corpus onto multiple files
    (its PUT endpoint rejects a single request above ~5 GiB); DuckHaven has
    no such size limit itself, but loading from the same multi-part files
    keeps both engines reading identical corpus data rather than a
    single-file version for one and a chunked one for the other.

    Stages and reads one part at a time (CREATE TABLE from the first,
    INSERT INTO for the rest) rather than staging every part up front and
    reading them all in one `read_parquet([...])` — confirmed live on
    SF100's ~27 GB `lineitem` as a single file: `stage_files`' presigned
    GET URL has a fixed ~900s expiry, and the CTAS on a 2 vCPU/4 GB agent
    took longer than that, so the URL went stale mid-read (`HTTP 403`)
    before the statement finished. Staging each part immediately before
    the statement that reads it keeps each URL's lifetime bounded to that
    one part's execution time instead of the whole table's."""
    start = time.monotonic()
    cursor = conn.cursor()
    first_url = _stage_and_upload(conn, local_paths[0])
    cursor.execute(f"CREATE TABLE {table} AS SELECT * FROM read_parquet('{first_url}')")
    for local_path in local_paths[1:]:
        url = _stage_and_upload(conn, local_path)
        cursor.execute(f"INSERT INTO {table} SELECT * FROM read_parquet('{url}')")
    cursor.execute(f"SELECT count(*) FROM {table}")
    row_count = cursor.fetchone()[0]
    return LoadResult(
        table=table, row_count=row_count, load_duration_ms=(time.monotonic() - start) * 1000
    )
