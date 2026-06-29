"""Liveness + readiness checks.

``/healthz`` is liveness: 200 when the process is up and Postgres is reachable.
Used by the compose healthcheck so ``docker compose ps`` reflects database
connectivity, not just process liveness.

``/readyz`` is readiness for load-balanced HA: 200 only when this replica can
serve real traffic — Postgres reachable, Polaris reachable, and not draining for
shutdown. A load balancer health-checks ``/readyz`` so it stops routing to a
replica that is shutting down or has lost a dependency, while ``/healthz`` keeps
the container alive.
"""

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_db, get_polaris_client
from api.services.polaris import PolarisClient

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


@router.get("/readyz")
async def readyz(
    request: Request,
    db: AsyncSession = Depends(get_db),
    polaris: PolarisClient = Depends(get_polaris_client),
) -> dict[str, str]:
    if getattr(request.app.state, "draining", False):
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="draining")
    try:
        await db.execute(text("SELECT 1"))
    except Exception as e:  # noqa: BLE001 — any DB failure means not ready
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"database unreachable: {type(e).__name__}",
        ) from e
    try:
        await polaris.ping()
    except Exception as e:  # noqa: BLE001 — any Polaris failure means not ready
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"polaris unreachable: {type(e).__name__}",
        ) from e
    return {"status": "ready"}
