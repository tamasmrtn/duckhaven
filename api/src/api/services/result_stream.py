"""Streaming a finished query's result as Parquet or as an Arrow IPC stream.

Results are Parquet files on the agent that ran the query (or, for a small
result-cache entry, bytes in Postgres). Parquet goes out as it is, relayed chunk
by chunk; Arrow is re-encoded from it batch by batch. Either way the API decodes
nothing but the result file itself, which invariant I1 allows.

The response body runs after the route returns, so everything here works from a
:class:`~api.services.query.ResultSource` resolved beforehand and opens its own
database session when it has to touch one.
"""

from __future__ import annotations

import base64
import contextlib
import io
import os
import tempfile
import time
import uuid
from collections.abc import AsyncIterator, Iterator

import httpx
import pyarrow as pa
import pyarrow.parquet as pq
from starlette.concurrency import iterate_in_threadpool, run_in_threadpool

from api.metrics import record_rows_decode, record_rows_proxy
from api.services import query as query_service
from api.services.query import ResultSource

PARQUET_MEDIA_TYPE = "application/vnd.apache.parquet"
ARROW_MEDIA_TYPE = "application/vnd.apache.arrow.stream"

# How often a long download tells the idle reaper the agent is still in use. A
# download is not a running query, so without this an elastic agent could be
# reaped, taking the result file with it, partway through.
_ACTIVITY_EVERY_S = 60.0
# Rows per Arrow record batch.
_ARROW_BATCH_ROWS = 65_536
# Rows per batch when encoding a result to inline: small, so encoding stops soon
# after the stream passes its cap, however wide the rows.
_INLINE_BATCH_ROWS = 4_096


async def _touch_agent(agent_id: uuid.UUID) -> None:
    from api.db.session import async_session_factory
    from api.services.compute.service import record_activity

    async with async_session_factory() as db:
        await record_activity(db, agent_id)
        await db.commit()


async def _relay(source: ResultSource, response: httpx.Response) -> AsyncIterator[bytes]:
    """The agent's bytes as they arrive, keeping an elastic agent marked busy."""
    elastic = source.agent is not None and source.agent.provider is not None
    last_touch = time.monotonic()
    async for chunk in response.aiter_bytes():
        yield chunk
        if elastic and time.monotonic() - last_touch >= _ACTIVITY_EVERY_S:
            await _touch_agent(source.agent.id)
            last_touch = time.monotonic()


async def open_parquet(source: ResultSource) -> tuple[AsyncIterator[bytes], dict[str, str]]:
    """The result as Parquet, byte for byte, and the headers to send with it.

    The upstream request is opened here, before the response starts, so a result
    that is gone or unreachable is still a 410/502/503 rather than a cut stream.
    """
    if source.inline is not None:
        inline = source.inline

        async def body() -> AsyncIterator[bytes]:
            yield inline

        return body(), {"Content-Length": str(len(inline))}

    client, response = await query_service.open_result_stream(source)
    headers = {}
    if "content-length" in response.headers:
        headers["Content-Length"] = response.headers["content-length"]

    async def stream() -> AsyncIterator[bytes]:
        started = time.perf_counter()
        try:
            async for chunk in _relay(source, response):
                yield chunk
        finally:
            # Here, not in a background task: Starlette skips those when the client
            # disconnects, which would leak the upstream connection.
            await response.aclose()
            await client.aclose()
            record_rows_proxy(time.perf_counter() - started)

    return stream(), headers


async def open_arrow(source: ResultSource) -> AsyncIterator[bytes]:
    """The result as an Arrow IPC stream.

    Parquet keeps its footer at the end, so the file is spooled to a temporary
    file first (inside the request, so a failed download is still an HTTP error)
    and then re-encoded one record batch at a time.
    """
    fd, path = tempfile.mkstemp(prefix="dh-result-", suffix=".parquet")
    try:
        with os.fdopen(fd, "wb") as spool:
            if source.inline is not None:
                spool.write(source.inline)
            else:
                client, response = await query_service.open_result_stream(source)
                started = time.perf_counter()
                try:
                    async for chunk in _relay(source, response):
                        spool.write(chunk)
                finally:
                    await response.aclose()
                    await client.aclose()
                    record_rows_proxy(time.perf_counter() - started)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(path)
        raise
    return _arrow_body(path)


async def _arrow_body(path: str) -> AsyncIterator[bytes]:
    batches = _arrow_batches(path)
    try:
        async for chunk in iterate_in_threadpool(batches):
            yield chunk
    finally:
        # The thread-pool wrapper does not close the generator it drives, so a
        # client that disconnects mid-stream would otherwise leak the spooled file.
        batches.close()
        with contextlib.suppress(FileNotFoundError):
            os.unlink(path)


def _arrow_batches(path: str) -> Iterator[bytes]:
    """Re-encode the Parquet file at ``path`` as Arrow IPC stream chunks."""
    started = time.monotonic()
    parquet = pq.ParquetFile(path)
    sink = io.BytesIO()

    def drain() -> bytes:
        data = sink.getvalue()
        sink.seek(0)
        sink.truncate()
        return data

    try:
        with pa.ipc.new_stream(sink, parquet.schema_arrow) as writer:
            for batch in parquet.iter_batches(batch_size=_ARROW_BATCH_ROWS):
                writer.write_batch(batch)
                if chunk := drain():
                    yield chunk
        # Closing the writer adds the schema (if no batch carried it) and the
        # end-of-stream marker.
        if chunk := drain():
            yield chunk
    finally:
        parquet.close()
        record_rows_decode(time.monotonic() - started, fmt="arrow")


# Bytes an Arrow column takes per row at the least, by the DuckDB type the
# result schema reports: the fixed width of its values, or the 4-byte offset of a
# variable-width one (strings, blobs, lists, maps). Types not listed, structs among
# them, count as nothing, so the sum is always a floor.
_ARROW_ROW_FLOOR = {
    "TINYINT": 1, "UTINYINT": 1, "SMALLINT": 2, "USMALLINT": 2,
    "INTEGER": 4, "UINTEGER": 4, "FLOAT": 4, "DATE": 4,
    "BIGINT": 8, "UBIGINT": 8, "DOUBLE": 8, "TIME": 8,
    "TIMESTAMP": 8, "TIMESTAMP WITH TIME ZONE": 8, "TIMESTAMP_S": 8, "TIMESTAMP_MS": 8,
    "TIMESTAMP_NS": 8, "INTERVAL": 12, "UUID": 16, "HUGEINT": 16, "UHUGEINT": 16,
    "VARCHAR": 4, "BLOB": 4, "JSON": 4, "BIT": 4,
}  # fmt: skip


def arrow_size_floor(row_count: int, schema: list[dict] | None) -> int:
    """A lower bound on a result's size as an Arrow stream, from its row count and
    column types alone, so a result certain not to fit is not read to find out."""
    per_row = 0
    for column in schema or []:
        kind = column.get("type") or ""
        if kind.startswith("DECIMAL("):
            per_row += 16  # read back from Parquet as decimal128, whatever the precision
        elif kind.endswith("]") or kind.startswith(("MAP(", "ENUM(")):
            per_row += 4  # lists, maps and enums arrive as offset-based arrays
        else:
            per_row += _ARROW_ROW_FLOOR.get(kind, 0)
    return row_count * per_row


async def read_arrow_inline(source: ResultSource, max_bytes: int) -> str | None:
    """The whole result as a base64 Arrow IPC stream, or None when it is larger
    than ``max_bytes`` as Parquet or as Arrow.

    For a statement response, so a small result costs the client no second call.
    Raises the HTTPException a failed read maps to, as the other readers do.
    """
    if source.inline is not None:
        data = source.inline
    else:
        client, response = await query_service.open_result_stream(source)
        started = time.perf_counter()
        chunks: list[bytes] = []
        size = 0
        try:
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > max_bytes:
                    return None
                chunks.append(chunk)
        finally:
            await response.aclose()
            await client.aclose()
            record_rows_proxy(time.perf_counter() - started)
        data = b"".join(chunks)
    if len(data) > max_bytes:
        return None
    ipc = await run_in_threadpool(_arrow_ipc_within, data, max_bytes)
    return None if ipc is None else base64.b64encode(ipc).decode("ascii")


def _arrow_ipc_within(data: bytes, max_bytes: int) -> bytes | None:
    """Re-encode a Parquet file held in memory as one Arrow IPC stream, or None as
    soon as the stream passes ``max_bytes``.

    Stopping early is what bounds the work: Parquet's dictionary and run-length
    encodings make a file a few KB on disk that is gigabytes as Arrow, and its
    footer does not say so in advance. Small batches keep the overshoot to one.
    """
    started = time.monotonic()
    parquet = pq.ParquetFile(pa.BufferReader(data))
    sink = pa.BufferOutputStream()
    try:
        with pa.ipc.new_stream(sink, parquet.schema_arrow) as writer:
            for batch in parquet.iter_batches(batch_size=_INLINE_BATCH_ROWS):
                writer.write_batch(batch)
                if sink.tell() > max_bytes:
                    return None
    finally:
        parquet.close()
        record_rows_decode(time.monotonic() - started, fmt="arrow")
    ipc = sink.getvalue()
    return None if ipc.size > max_bytes else ipc.to_pybytes()
