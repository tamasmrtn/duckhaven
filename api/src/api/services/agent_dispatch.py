"""Cross-replica agent dispatch.

The agent registry (``ConnectionManager``) only knows the WebSockets connected to
*this* API replica. With multiple replicas behind a load balancer, an agent's
socket is pinned to whichever replica it dialed, but a query can be created on any
replica. This module bridges that gap:

* **Presence** is tracked in Postgres: ``Agent.owner_url`` records which replica
  holds the socket, and ``last_ping_at`` proves the ownership is live. So any
  replica can answer "which agents are connected" by reading the DB.
* **Sending** a frame to an agent goes directly over the local socket when this
  replica owns it; otherwise it is forwarded over HTTP to the owning replica's
  network-private ``/internal`` endpoint, which puts it on the socket.

Result frames already flow back replica-agnostically (the owning replica writes
``QUERY_DONE`` to Postgres; result Parquet is fetched over direct HTTP), so only
the outbound path needs routing.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.config import settings
from api.models.agent import Agent
from api.services.agent_registry import registry

logger = logging.getLogger(__name__)

_FORWARD_TIMEOUT_S = 5.0


async def claim_agent_owner(db: AsyncSession, agent_id: uuid.UUID) -> None:
    """Record this replica as the owner of ``agent_id``'s socket (on register)."""
    await db.execute(
        sa.update(Agent)
        .where(Agent.id == agent_id)
        .values(
            owner_id=settings.replica_id,
            owner_url=settings.replica_internal_url,
            status="healthy",
            last_ping_at=datetime.now(tz=UTC),
        )
    )
    await db.commit()


async def release_agent_owner(db: AsyncSession, agent_id: uuid.UUID) -> None:
    """Clear ownership so no replica is considered to hold the socket anymore."""
    await db.execute(
        sa.update(Agent)
        .where(Agent.id == agent_id)
        .values(owner_id=None, owner_url=None, status="unavailable")
    )
    await db.commit()


async def connected_agent_ids(db: AsyncSession) -> set[str]:
    """Agent ids that are connected somewhere in the cluster.

    An agent counts as connected when it owns a replica and has pinged within the
    presence TTL — the freshness check covers a replica that died without clearing
    its ownership rows.
    """
    cutoff = datetime.now(tz=UTC) - timedelta(seconds=settings.agent_presence_ttl_s)
    rows = (
        await db.execute(
            sa.select(Agent.id).where(
                Agent.owner_url.is_not(None),
                Agent.last_ping_at.is_not(None),
                Agent.last_ping_at >= cutoff,
            )
        )
    ).scalars()
    # Sockets held by this replica are authoritative even before the ownership
    # write lands, so union them with the DB view of peer-owned agents.
    return {str(aid) for aid in rows} | registry.connected_ids()


async def is_agent_connected(db: AsyncSession, agent_id: uuid.UUID) -> bool:
    """Whether a specific agent is connected anywhere in the cluster."""
    if registry.get(agent_id) is not None:
        return True
    cutoff = datetime.now(tz=UTC) - timedelta(seconds=settings.agent_presence_ttl_s)
    row = await db.execute(
        sa.select(Agent.id).where(
            Agent.id == agent_id,
            Agent.owner_url.is_not(None),
            Agent.last_ping_at.is_not(None),
            Agent.last_ping_at >= cutoff,
        )
    )
    return row.scalar_one_or_none() is not None


async def send_to_agent(db: AsyncSession, agent_id: uuid.UUID, payload: str) -> bool:
    """Deliver a frame to an agent wherever its socket lives.

    Returns ``True`` if the frame was handed to the socket. A local socket is used
    directly; otherwise the frame is forwarded to the owning replica. A dead owner
    (forward fails) returns ``False`` so the caller fails fast — the agent will
    reconnect to a live replica and repoint its ownership.
    """
    if registry.get(agent_id) is not None:
        return await registry.send(agent_id, payload)

    agent = await db.get(Agent, agent_id)
    if agent is None or not agent.owner_url:
        return False
    if agent.owner_url == settings.replica_internal_url:
        # We are the recorded owner but the socket is gone (stale ownership).
        return False
    if not settings.internal_api_secret:
        # Forwarding disabled (single-replica mode): the agent is unreachable here.
        return False

    url = f"{agent.owner_url.rstrip('/')}/internal/agents/{agent_id}/send"
    try:
        async with httpx.AsyncClient(timeout=_FORWARD_TIMEOUT_S) as client:
            resp = await client.post(
                url,
                json={"payload": payload},
                headers={"X-Internal-Secret": settings.internal_api_secret},
            )
        return resp.status_code == 200 and resp.json().get("delivered") is True
    except httpx.HTTPError as exc:
        logger.warning("Forward to owner for agent %s failed: %s", agent_id, exc)
        return False


async def disconnect_agent(db: AsyncSession, agent_id: uuid.UUID) -> bool:
    """Force an agent's socket closed wherever it lives (admin disconnect)."""
    if registry.get(agent_id) is not None:
        return await registry.close(agent_id)
    agent = await db.get(Agent, agent_id)
    if agent is None or not agent.owner_url:
        return False
    if agent.owner_url == settings.replica_internal_url or not settings.internal_api_secret:
        return False
    url = f"{agent.owner_url.rstrip('/')}/internal/agents/{agent_id}/disconnect"
    try:
        async with httpx.AsyncClient(timeout=_FORWARD_TIMEOUT_S) as client:
            resp = await client.post(
                url, headers={"X-Internal-Secret": settings.internal_api_secret}
            )
        return resp.status_code == 200
    except httpx.HTTPError as exc:
        logger.warning("Disconnect forward for agent %s failed: %s", agent_id, exc)
        return False


async def drain_local_agents(session_factory: async_sessionmaker[AsyncSession]) -> None:
    """Close every socket this replica holds and clear its ownership.

    Called on graceful shutdown: closing with 1012 (Service Restart) prompts each
    agent to reconnect to a live replica immediately, and clearing ownership means
    no query is routed to this dying replica in the meantime.
    """
    ids = [uuid.UUID(a) for a in registry.connected_ids()]
    for agent_id in ids:
        await registry.close(agent_id)
    if ids:
        async with session_factory() as db:
            await db.execute(
                sa.update(Agent)
                .where(Agent.id.in_(ids))
                .values(owner_id=None, owner_url=None, status="unavailable")
            )
            await db.commit()


async def gather_agent_metrics(db: AsyncSession) -> dict[str, list[dict]]:
    """Live metric ring buffers for every connected agent, across replicas.

    Local sockets are read from the in-memory buffer; agents owned by peers are
    fetched from those replicas' internal metrics endpoints. Peers that don't
    answer are simply omitted (metrics are a best-effort live view).
    """
    buffers = dict(registry.recent_metrics())
    if not settings.internal_api_secret:
        return buffers

    cutoff = datetime.now(tz=UTC) - timedelta(seconds=settings.agent_presence_ttl_s)
    peers = (
        await db.execute(
            sa.select(Agent.id, Agent.owner_url).where(
                Agent.owner_url.is_not(None),
                Agent.owner_url != settings.replica_internal_url,
                Agent.last_ping_at.is_not(None),
                Agent.last_ping_at >= cutoff,
            )
        )
    ).all()
    for agent_id, owner_url in peers:
        url = f"{owner_url.rstrip('/')}/internal/agents/{agent_id}/metrics"
        try:
            async with httpx.AsyncClient(timeout=_FORWARD_TIMEOUT_S) as client:
                resp = await client.get(
                    url, headers={"X-Internal-Secret": settings.internal_api_secret}
                )
            if resp.status_code == 200:
                samples = resp.json().get("metrics") or []
                if samples:
                    buffers[str(agent_id)] = samples
        except httpx.HTTPError as exc:
            logger.warning("Metrics fetch for agent %s failed: %s", agent_id, exc)
    return buffers
