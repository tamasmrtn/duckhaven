"""Prometheus metrics exposition.

Unauthenticated like the health endpoints (Prometheus scrapers carry no session
cookie); keep it on the internal network. Disabled when ``METRICS_ENABLED`` is
false. See ``api/src/api/metrics.py`` for the instruments and HA design.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import Response

from api import metrics
from api.config import settings
from api.deps import get_db

router = APIRouter()


@router.get("/metrics")
async def metrics_endpoint(db: AsyncSession = Depends(get_db)) -> Response:
    """Prometheus exposition for this replica. Unauthenticated.

    404 when metrics are disabled, so a scrape against a deployment that does not
    export them fails cleanly rather than returning an empty body."""
    if not settings.metrics_enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    payload, content_type = await metrics.render(db)
    return Response(content=payload, media_type=content_type)
