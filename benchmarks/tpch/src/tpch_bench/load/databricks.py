"""Loads a generated TPC-H corpus into Databricks.

Unlike DuckHaven's session-scoped presigned staging, Databricks has no
per-session upload surface — a Unity Catalog Volume is the closest
equivalent, and it's the workspace's own recommended way to land files
for `COPY INTO`/`read_files()`. Each generated Parquet file is uploaded
via the Files API (`PUT /api/2.0/fs/files/...`, raw bytes, no
multipart/staging dance needed at SF1's file sizes) into a volume this
module creates on first use, then `CREATE TABLE ... AS SELECT * FROM
read_files(...)` runs through the same SQL warehouse connection
`DatabricksClient` uses — a real client operation, not a side channel.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import httpx

from tpch_bench.clients.databricks import fetch_oauth_token
from tpch_bench.datagen.tpchgen_runner import TABLES

if TYPE_CHECKING:
    from databricks.sql.client import Connection

_UPLOAD_TIMEOUT_S = 300.0


@dataclass(frozen=True)
class LoadResult:
    table: str
    row_count: int
    load_duration_ms: float


class DatabricksLoader:
    """One Files-API/SQL-warehouse pairing for loading a corpus into one
    (catalog, schema). Mints its own OAuth token per upload (Databricks
    tokens are short-lived; see `clients/databricks.py`'s docstring on why
    this project doesn't refresh one under a long-lived connection)."""

    def __init__(
        self,
        *,
        server_hostname: str,
        client_id: str,
        client_secret: str,
        catalog: str,
        schema: str,
        volume: str = "corpus",
    ) -> None:
        self._server_hostname = server_hostname
        self._client_id = client_id
        self._client_secret = client_secret
        self._catalog = catalog
        self._schema = schema
        self._volume = volume

    def _token(self) -> str:
        return fetch_oauth_token(
            server_hostname=self._server_hostname,
            client_id=self._client_id,
            client_secret=self._client_secret,
        )

    def _volume_path(self, filename: str) -> str:
        return f"/Volumes/{self._catalog}/{self._schema}/{self._volume}/{filename}"

    def ensure_volume(self, conn: Connection) -> None:
        cursor = conn.cursor()
        cursor.execute(f"CREATE VOLUME IF NOT EXISTS {self._catalog}.{self._schema}.{self._volume}")

    def upload(self, local_path: Path) -> str:
        """PUT `local_path` into the volume; returns its `/Volumes/...` path."""
        volume_path = self._volume_path(local_path.name)
        with httpx.Client(timeout=_UPLOAD_TIMEOUT_S) as client:
            with local_path.open("rb") as body:
                response = client.put(
                    f"https://{self._server_hostname}/api/2.0/fs/files{volume_path}",
                    headers={"Authorization": f"Bearer {self._token()}"},
                    params={"overwrite": "true"},
                    content=body,
                )
        response.raise_for_status()
        return volume_path

    def load_table(self, conn: Connection, *, table: str, local_path: Path) -> LoadResult:
        """CREATE TABLE `table` from a single unpartitioned Parquet file.
        Fails if `table` already exists — callers that need idempotent
        reloads should DROP TABLE first."""
        start = time.monotonic()
        volume_path = self.upload(local_path)
        cursor = conn.cursor()
        cursor.execute(
            f"CREATE TABLE {table} AS SELECT * FROM "
            f"read_files('{volume_path}', format => 'parquet')"
        )
        cursor.execute(f"SELECT count(*) AS n FROM {table}")
        row_count = cursor.fetchall()[0][0]
        return LoadResult(
            table=table, row_count=row_count, load_duration_ms=(time.monotonic() - start) * 1000
        )

    def load_corpus(
        self, conn: Connection, corpus_dir: Path, *, tables: tuple[str, ...] = TABLES
    ) -> list[LoadResult]:
        """Load every unpartitioned `<table>.parquet` under `corpus_dir`
        (as `datagen.generate()` lays it out for `parts=None`)."""
        self.ensure_volume(conn)
        results = []
        for table in tables:
            local_path = corpus_dir / f"{table}.parquet"
            results.append(self.load_table(conn, table=table, local_path=local_path))
        return results
