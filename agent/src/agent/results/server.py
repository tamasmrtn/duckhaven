import re
from pathlib import Path

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Route

_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def make_results_app(results_dir: Path, session_token: str) -> Starlette:
    async def get_result(request: Request) -> Response:
        auth = request.headers.get("Authorization", "")
        if auth != f"Bearer {session_token}":
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

        return FileResponse(path, media_type="application/octet-stream")

    return Starlette(routes=[Route("/results/{filename}", get_result)])
