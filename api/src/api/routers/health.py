"""Liveness + readiness check.

Returns 200 when the api process is up AND postgres is reachable. Used by the
compose healthcheck so `docker compose ps` reflects database connectivity, not
just process liveness.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_db

router = APIRouter()


@router.get("/healthz")
async def healthz(db: AsyncSession = Depends(get_db)) -> dict[str, str]:
    try:
        await db.execute(text("SELECT 1"))
    except Exception as e:  # noqa: BLE001 — surface any DB failure as 503
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"database unreachable: {type(e).__name__}",
        ) from e
    return {"status": "ok"}
