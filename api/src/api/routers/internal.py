"""Network-private endpoints for inter-replica agent dispatch.

Mounted on the outer app (not under ``/api``). A peer replica calls these to put
a frame on, read metrics from, or close an agent WebSocket that *this* replica
owns. They are guarded by a shared secret and must never be exposed past the
internal network — the load balancer routes only browser/agent traffic, not
``/internal``.
"""

import uuid

from fastapi import APIRouter, Header, HTTPException, status
from pydantic import BaseModel

from api.config import settings
from api.services.agent_registry import registry

router = APIRouter(prefix="/internal", tags=["internal"])


def _authorize(secret: str | None) -> None:
    configured = settings.internal_api_secret
    if not configured or secret != configured:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)


class SendFrame(BaseModel):
    payload: str


@router.post("/agents/{agent_id}/send")
async def forward_send(
    agent_id: uuid.UUID,
    body: SendFrame,
    x_internal_secret: str | None = Header(default=None),
) -> dict[str, bool]:
    _authorize(x_internal_secret)
    delivered = await registry.send(agent_id, body.payload)
    return {"delivered": delivered}


@router.get("/agents/{agent_id}/metrics")
async def forward_metrics(
    agent_id: uuid.UUID,
    x_internal_secret: str | None = Header(default=None),
) -> dict[str, list[dict]]:
    _authorize(x_internal_secret)
    return {"metrics": registry.recent_metrics().get(str(agent_id), [])}


@router.post("/agents/{agent_id}/disconnect")
async def forward_disconnect(
    agent_id: uuid.UUID,
    x_internal_secret: str | None = Header(default=None),
) -> dict[str, bool]:
    _authorize(x_internal_secret)
    return {"disconnected": await registry.close(agent_id)}
