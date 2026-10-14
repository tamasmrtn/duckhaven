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
from starlette.concurrency import iterate_in_threadpool

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
