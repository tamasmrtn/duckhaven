import asyncio
import os
import re
import tempfile
from pathlib import Path

import duckdb
from starlette.applications import Starlette
from starlette.background import BackgroundTask
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Route

from agent.auth import TokenHolder

_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def _parse_window(request: Request) -> tuple[int, int] | None:
    """Parse the optional row_offset/row_limit query params into a window.

    Returns None when neither is present (serve the whole file unchanged) or
    raises ValueError when a value is present but not a non-negative int.
    """
    raw_offset = request.query_params.get("row_offset")
    raw_limit = request.query_params.get("row_limit")
    if raw_offset is None and raw_limit is None:
        return None
    offset = int(raw_offset) if raw_offset is not None else 0
    # A missing limit with a present offset means "the rest"; -1 is DuckDB's
    # sentinel for an unbounded LIMIT.
    limit = int(raw_limit) if raw_limit is not None else -1
    if offset < 0 or (limit < 0 and limit != -1):
        raise ValueError("row_offset and row_limit must be non-negative")
    return offset, limit


def _slice_parquet(source: Path, offset: int, limit: int) -> str:
    """Write just rows [offset, offset+limit) of `source` to a new temp Parquet.

    Runs on a worker thread (DuckDB is blocking). The temp file lives in the
    system temp dir — not the results dir — so the retention sweep never touches
    it; the caller deletes it once the response is sent.
    """
    fd, tmp = tempfile.mkstemp(suffix=".parquet")
    os.close(fd)
    conn = duckdb.connect()
    try:
        # COPY cannot bind its TO target, and offset/limit are validated ints, so
        # inline them. The source path is the uuid-validated results file and tmp
        # comes from mkstemp — neither is user-supplied (same pattern as the
        # runner's COPY ... TO).
        conn.execute(
            f"COPY (SELECT * FROM read_parquet('{source}') LIMIT {limit} OFFSET {offset}) "
            f"TO '{tmp}' (FORMAT PARQUET)"
        )
    finally:
        conn.close()
    return tmp


def make_results_app(results_dir: Path, token_holder: TokenHolder) -> Starlette:
    async def get_result(request: Request) -> Response:
        expected = token_holder.value
        auth = request.headers.get("Authorization", "")
        if not expected or auth != f"Bearer {expected}":
            return JSONResponse({"detail": "Unauthorized"}, status_code=401)

        filename = request.path_params["filename"]
        if not filename.endswith(".parquet"):
            return JSONResponse({"detail": "Not found"}, status_code=404)
        stem = filename[: -len(".parquet")]
        if not _UUID_RE.match(stem):
            return JSONResponse({"detail": "Not found"}, status_code=404)

        path = results_dir / filename
        if not path.exists():
            return JSONResponse({"detail": "Not found"}, status_code=404)

        try:
            window = _parse_window(request)
        except ValueError as exc:
            return JSONResponse({"detail": str(exc)}, status_code=400)

        # No window requested: stream the whole file as before.
        if window is None:
            return FileResponse(path, media_type="application/octet-stream")

        # Slice the requested row window locally so the control plane only ever
        # downloads one page, never the whole result. The X-DH-Row-Offset header
        # tells the caller the returned file already starts at the window, so it
        # decodes at offset 0.
        offset, limit = window
        tmp = await asyncio.to_thread(_slice_parquet, path, offset, limit)
        return FileResponse(
            tmp,
            media_type="application/octet-stream",
            headers={"X-DH-Row-Offset": str(offset)},
            background=BackgroundTask(os.unlink, tmp),
        )

    return Starlette(routes=[Route("/results/{filename}", get_result)])
