"""Dashboards.

For now this serves the dashboard file format itself, so editors, the CLI and AI
clients can validate a file before any dashboard is stored.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import JSONResponse

from api.services.dashboards.spec import SPEC_VERSION, SUPPORTED_SPEC_VERSIONS, json_schema

router = APIRouter()


@router.get("/dashboard-schema", response_class=JSONResponse)
async def get_dashboard_schema(
    version: int = Query(default=SPEC_VERSION, description="The format version to describe."),
) -> JSONResponse:
    """The JSON Schema of a dashboard file. Unauthenticated.

    Point an editor's YAML language server at it for completion and inline
    errors. It covers each file's shape; references between parts of the file,
    such as a `$name` naming a declared parameter, are checked when the
    dashboard is validated. A version this server does not read is a 422
    `unsupported_spec_version`."""
    if version not in SUPPORTED_SPEC_VERSIONS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "error": "unsupported_spec_version",
                "detail": (
                    f"This server reads dashboard format version "
                    f"{', '.join(map(str, SUPPORTED_SPEC_VERSIONS))}, not {version}."
                ),
            },
        )
    return JSONResponse(json_schema(), headers={"Cache-Control": "public, max-age=3600"})
