import json
import logging
import secrets
import uuid
from datetime import UTC, datetime

import sqlalchemy as sa
from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.deps import get_session_factory
from api.models.agent import Agent
from api.models.user import Credential
from api.services.agent_dispatch import claim_agent_owner, release_agent_owner
from api.services.agent_registry import registry
from api.services.agent_telemetry import (
    accumulate,
    flush_minute,
    purge_expired_metrics,
    record_lifecycle_event_now,
    take_pending,
)
from duckhaven_shared.protocol import Frame, FrameType

logger = logging.getLogger(__name__)

router = APIRouter()

# How often a heartbeat refreshes the DB ``last_ping_at`` that proves cluster-wide
# presence. Heartbeats arrive far more often; throttling avoids a write per beat.
_PRESENCE_REFRESH_S = 30.0


def _result_host(ws: WebSocket) -> str | None:
    """The agent's reachable address for result fetches.

    Behind a reverse proxy (Caddy in the HA topology) the socket peer is the
    proxy, so the real agent address is the left-most ``X-Forwarded-For`` hop.
    Falls back to the socket peer for a direct (single-node) connection.
    """
    forwarded_for = ws.headers.get("x-forwarded-for")
    if forwarded_for:
        first = forwarded_for.split(",", 1)[0].strip()
        if first:
            return first
    return ws.client.host if ws.client else None


async def _elastic_result_host(
    db: AsyncSession, agent_id: uuid.UUID | None
) -> tuple[str | None, bool]:
    """The backend-reported address of an elastic agent's result server, and whether the
    agent is elastic at all.

    Neither mechanism above works for an agent on a private network: it cannot be told
    its own address (that is assigned after its configuration is fixed) and its socket
    reaches us through a NAT gateway, so the peer and the ``X-Forwarded-For`` hop are
    both the gateway. The cloud is the only authority, so ask it.

    The address may legitimately be unknown here, because it is assigned after the
    instance is created and the agent can dial home before ARM reports it. The caller
    uses the second element to decide what to do with that: for an elastic agent no
    address is better than the wrong one.
    """
    if agent_id is None:
        return None, False
    agent = await db.get(Agent, agent_id)
    if agent is None or agent.provider is None:
        return None, False
    # Lazy, matching the bind_queued_work import below: keeps this router free of a
    # load-time dependency on the compute stack.
    from api.services.compute.service import resolve_result_host

    return await resolve_result_host(agent), True


@router.websocket("/agents/connect")
async def agent_connect(
    ws: WebSocket,
    session_factory: async_sessionmaker = Depends(get_session_factory),
) -> None:
    await ws.accept()
    agent_id: uuid.UUID | None = None

    try:
        raw = await ws.receive_text()
        frame = Frame.model_validate_json(raw)
        if frame.type != "auth" or "token" not in frame.payload:
            await ws.close(code=1008, reason="Expected auth frame")
            return

        token = frame.payload["token"]
        # Where the agent's result server is reachable, so services/query.proxy_rows
        # can fetch result Parquet. The agent advertises its result port in the auth
        # frame; the host is the connection's peer address — except behind a reverse
        # proxy / load balancer (the HA topology dials the API through Caddy), where
        # the peer is the proxy. There the agent's real address is the left-most
        # X-Forwarded-For hop, mirroring how the add-agent dial URL trusts
        # X-Forwarded-* (routers/agents._agent_dial_url).
        # An agent whose result server is reached at a different address than its
        # socket peer advertises it explicitly; otherwise the peer/X-Forwarded-For hop
        # is used. Elastic agents fall between the two: see _resolve_result_host.
        advertised_host = frame.payload.get("result_host")
        result_port = frame.payload.get("result_port")
        result_port_int = int(result_port) if result_port is not None else None

        # The handshake runs in a single short-lived session; the connection is
        # returned to the pool before we enter the (potentially hours-long) loop.
        async with session_factory() as db:
            # The agent sends a single token: its persisted session token on a
            # reconnect, or the single-use bootstrap token on its first
            # registration. We accept both kinds and branch on the credential's
            # kind below so one logical agent maps to one row across restarts and
            # network blips.
            result = await db.execute(
                select(Credential).where(
                    Credential.token == token,
                    Credential.kind.in_(("agent_bootstrap", "agent_session")),
                )
            )
            cred = result.scalar_one_or_none()
            if cred is None:
                await ws.close(code=1008, reason="Invalid token")
                return

            elastic_host, is_elastic = await _elastic_result_host(db, cred.agent_id)
            if advertised_host:
                result_host = advertised_host
            elif is_elastic:
                # Deliberately not falling back to the socket peer: an elastic agent
                # reaches us through a NAT gateway, so the peer is the gateway, and
                # storing it makes every result fetch hang until it times out. Leaving
                # it unset fails fast instead, and compute.service.ensure_result_host
                # fills it in when the address is first needed.
                result_host = elastic_host
            else:
                result_host = _result_host(ws)

            if cred.kind == "agent_session":
                # Re-authentication: rebind the existing agent row instead of
                # minting a new one. The session token is long-lived and is not
                # consumed.
                agent_id = cred.agent_id
                # Reset the idle clock on reconnect so a just-reconnected elastic
                # agent isn't reaped against a stale last_active_at (harmless for
                # static agents, which are never reaped).
                values: dict[str, object] = {
                    "status": "healthy",
                    "last_active_at": datetime.now(tz=UTC),
                    "result_port": result_port_int,
                }
                # Only overwrite a known address with another one. resolve_result_host
                # returns None on any transient cloud error, so writing it
                # unconditionally would blank an address that was already correct and
                # break result fetches until something resolved it again.
                if result_host is not None:
                    values["result_host"] = result_host
                await db.execute(sa.update(Agent).where(Agent.id == agent_id).values(**values))
                session_token = token
                await db.commit()
            else:
                # First registration: the bootstrap token is single-use.
                boot_agent_id = cred.agent_id
                await db.delete(cred)
                if boot_agent_id is not None:
                    # Elastic: the token was minted for a pre-created row (see
                    # compute.service.ensure_agent). Rebind that row instead of
                    # minting a new one — it is the row that provisioned the
                    # instance — and flip its lifecycle provisioning -> running.
                    # Start the idle clock now (last_active_at): a freshly
                    # registered agent must get a full idle window even if it never
                    # runs work, rather than being reaped against provisioned_at
                    # (which is already old after a slow cold start).
                    agent_id = boot_agent_id
                    # Only a row still expecting this instance may be revived. The
                    # reaper fails a row that misses its provisioning deadline and
                    # terminates the instance behind it; an unguarded update would let
                    # a container that dials home afterwards flip that row back to
                    # running while nothing is running, so the picker would offer an
                    # agent that does not exist and queries sent to it would hang.
                    revived = await db.execute(
                        sa.update(Agent)
                        .where(
                            Agent.id == agent_id,
                            Agent.lifecycle.in_(("provisioning", "running")),
                        )
                        .values(
                            status="healthy",
                            lifecycle="running",
                            last_active_at=datetime.now(tz=UTC),
                            result_host=result_host,
                            result_port=result_port_int,
                        )
                    )
                    if revived.rowcount == 0:
                        # Its token is spent (deleted above), so this cannot be retried.
                        logger.warning(
                            "Refusing registration for agent %s: no longer awaiting one",
                            agent_id,
                        )
                        await db.commit()
                        await ws.close(code=1008)
                        return
                else:
                    label = frame.payload.get("name", f"agent-{secrets.token_hex(4)}")
                    agent = Agent(
                        name=label,
                        status="healthy",
                        result_host=result_host,
                        result_port=result_port_int,
                    )
                    db.add(agent)
                    await db.flush()
                    agent_id = agent.id

                # An agent has exactly one live session credential. Restarting an
                # elastic agent reuses its row and enrolls again with a fresh bootstrap
                # token, so without clearing the previous one the row accumulates
                # credentials and services/query.agent_session_token — which expects at
                # most one — fails the next result fetch outright.
                await db.execute(
                    sa.delete(Credential).where(
                        Credential.agent_id == agent_id,
                        Credential.kind == "agent_session",
                    )
                )
                session_token = secrets.token_urlsafe(32)
                session_cred = Credential(
                    user_id=None,
                    agent_id=agent_id,
                    kind="agent_session",
                    token=session_token,
                    expires_at=None,
                )
                db.add(session_cred)
                await db.commit()

        await ws.send_text(
            json.dumps(
                {
                    "type": "auth_ok",
                    "payload": {
                        "agent_id": str(agent_id),
                        "session_token": session_token,
                    },
                }
            )
        )

        registry.register(agent_id, ws)
        # Record this replica as the socket's owner so queries created on any
        # replica can route dispatch frames here.
        async with session_factory() as db:
            await claim_agent_owner(db, agent_id)
            # Recorded for static agents too: "the socket was up and this agent
            # could serve work" is the same fact for both kinds, and it is what the
            # monitoring page's running/not-running timeline is built from.
            await record_lifecycle_event_now(db, agent_id, "connected")
            # If an elastic agent just came up, dispatch any queued work parked
            # while it was provisioning.
            agent_row = await db.get(Agent, agent_id)
            if agent_row is not None and agent_row.provider is not None:
                from api.services.compute.service import (
                    bind_queued_work,
                    bind_scheduled_work,
                )

                await bind_queued_work(db, agent_row)
                # Scheduled runs parked while this agent was restarted for them.
                # Separate from the pool binder: those match a pool key, these
                # match the agent a schedule explicitly names.
                await bind_scheduled_work(db, agent_row)
        last_presence_refresh = datetime.now(tz=UTC)

        async for raw_msg in ws.iter_text():
            # Each frame is isolated: a per-frame session keeps no pooled
            # connection between frames, and a failed write can't poison the next
            # frame. A frame whose handling raises is logged and skipped rather
            # than tearing down the socket.
            try:
                msg_frame = Frame.model_validate_json(raw_msg)

                if msg_frame.type == FrameType.HEARTBEAT:
                    registry.touch(agent_id)
                    now = datetime.now(tz=UTC)
                    if (now - last_presence_refresh).total_seconds() >= _PRESENCE_REFRESH_S:
                        async with session_factory() as db:
                            await db.execute(
                                sa.update(Agent)
                                .where(Agent.id == agent_id)
                                .values(last_ping_at=now)
                            )
                            await db.commit()
                        last_presence_refresh = now
                    await ws.send_text(Frame(type=FrameType.HEARTBEAT).model_dump_json())

                elif msg_frame.type == FrameType.AGENT_STATUS:
                    async with session_factory() as db:
                        await db.execute(
                            sa.update(Agent)
                            .where(Agent.id == agent_id)
                            .values(
                                capabilities=msg_frame.payload,
                                status="healthy",
                                last_ping_at=datetime.now(tz=UTC),
                            )
                        )
                        await db.commit()

                elif msg_frame.type == FrameType.METRICS_SAMPLE:
                    # High-frequency live utilization: the ring buffer keeps the last
                    # ~5 minutes at full 2s resolution for the live view.
                    registry.record_metrics(agent_id, msg_frame.payload)
                    # The same samples, folded into a per-minute accumulator that is
                    # written once the minute closes. That is what the monitoring
                    # page's 1-24h windows read; the ring buffer cannot span them.
                    closed = accumulate(agent_id, msg_frame.payload)
                    if closed is not None:
                        async with session_factory() as db:
                            await flush_minute(db, agent_id, closed)
                            await purge_expired_metrics(db)
                    # Metrics arrive every couple of seconds, so they're a
                    # reliable liveness signal: refresh the cluster-wide presence
                    # watermark (throttled) so peer replicas see this agent as
                    # connected and can route dispatch frames to this replica.
                    # Without this, last_ping_at is set only at registration and
                    # goes stale after the presence TTL, breaking cross-replica
                    # dispatch from any non-owning replica.
                    registry.touch(agent_id)
                    now = datetime.now(tz=UTC)
                    if (now - last_presence_refresh).total_seconds() >= _PRESENCE_REFRESH_S:
                        async with session_factory() as db:
                            await db.execute(
                                sa.update(Agent)
                                .where(Agent.id == agent_id)
                                .values(last_ping_at=now)
                            )
                            await db.commit()
                        last_presence_refresh = now

                elif msg_frame.type in (FrameType.QUERY_DONE, FrameType.QUERY_PROGRESS):
                    from api.services.query import handle_agent_frame

                    async with session_factory() as db:
                        await handle_agent_frame(db, msg_frame)

                elif msg_frame.type in (FrameType.SESSION_OPENED, FrameType.SESSION_CLOSED):
                    from api.services.sql_sessions.service import handle_session_frame

                    async with session_factory() as db:
                        await handle_session_frame(db, msg_frame)

                elif msg_frame.type == FrameType.STATEMENT_ACK:
                    from api.services.sql_sessions.service import handle_statement_ack

                    async with session_factory() as db:
                        await handle_statement_ack(db, msg_frame)
            except WebSocketDisconnect:
                raise
            except Exception:
                logger.exception("Failed to handle agent frame for agent %s", agent_id)

    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("Agent WebSocket handler failed for agent %s", agent_id)
    finally:
        if agent_id:
            registry.unregister(agent_id)
            async with session_factory() as db:
                await release_agent_owner(db, agent_id)
                await record_lifecycle_event_now(db, agent_id, "disconnected")
                # Write the minute still open when the socket dropped — the one an
                # operator looks at first after an agent goes away.
                pending = take_pending(agent_id)
                if pending is not None:
                    await flush_minute(db, agent_id, pending)
                # Reconcile SQL sessions: this agent's held connections are gone, so
                # its non-terminal sessions can't continue (Postgres decides — I9).
                from api.services.sql_sessions.service import fail_sessions_for_agent

                await fail_sessions_for_agent(db, agent_id)
