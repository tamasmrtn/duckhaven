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
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from api.config import settings
from api.deps import get_db, get_polaris_client
from api.services.polaris import PolarisClient

router = APIRouter()

# API contract version — bump only on a breaking change to the API contract,
# not per release (the release/build version is settings.app_version).
API_VERSION = 1


class VersionOut(BaseModel):
    # Release/build version (git tag) — provenance, "what build is running".
    version: str
    # API contract version — negotiated compatibility, bumped only on breaks.
    api_version: int


@router.get("/version")
async def version() -> VersionOut:
    """The running build and the API contract version. Unauthenticated.

    `version` moves with every release and identifies the build; `api_version` is
    a single integer bumped only when a change breaks the contract on the wire.
    A server old enough to lack this endpoint returns 404 -- treat that as the
    oldest supported version."""
    return VersionOut(version=settings.app_version, api_version=API_VERSION)


@router.get("/healthz")
async def healthz(db: AsyncSession = Depends(get_db)) -> dict[str, str]:
    """Liveness: is this process up and can it reach its database?

    503 when the database is unreachable. Does not check Polaris -- an upstream
    outage should not make an otherwise healthy replica get restarted."""
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
    """Readiness: should this replica receive traffic?

    Stricter than `/healthz`: 503 while draining, and 503 if either the database
    or Polaris is unreachable, because a replica that cannot reach the catalog
    cannot serve a request end to end."""
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
