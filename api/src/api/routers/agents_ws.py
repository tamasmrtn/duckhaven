import asyncio
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
from api.services.agent_presence import close_unfinished_run
from api.services.agent_registry import registry
from api.services.agent_telemetry import (
    accumulate,
    flush_minute,
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


async def _on_first_report(db: AsyncSession, agent_id: uuid.UUID) -> None:
    """Act on what a freshly connected agent says it is.

    An elastic agent that turns out to be the wrong runtime is terminated when it
    belongs to a pool: it would otherwise count against the pool's cap while being
    refused every piece of work, and the pool would never provision a replacement.
    Otherwise, work parked while it was starting is dispatched to it — each piece
    still subject to the dispatch checks, now that its capabilities are known.
    """
    agent_row = await db.get(Agent, agent_id)
    if agent_row is None:
        return
    from api.services.runtimes import DISPATCHABLE_STATES, resolve

    state = resolve(agent_row).state
    if state not in DISPATCHABLE_STATES:
        logger.warning(
            "Agent %s reports an unsupported runtime (%s): %s",
            agent_id,
            state,
            {k: (agent_row.capabilities or {}).get(k) for k in ("runtime_id", "engine_version")},
        )
    if agent_row.provider is None:
        return

    from api.services.compute.service import (
        bind_pending_sessions,
        bind_queued_work,
        bind_scheduled_work,
        bind_targeted_work,
        terminate_agent,
    )

    if agent_row.pool_key is not None and state in ("mismatch", "unrecognized"):
        await terminate_agent(db, agent_row, reason="runtime_mismatch")
        return
    # Sessions first: a parked session has a client blocked on an open HTTP
    # request, while a parked run's client is already polling.
    await bind_pending_sessions(db, agent_row)
    await bind_queued_work(db, agent_row)
    # Interactive runs submitted against this agent while it was terminated,
    # which restarted it.
    await bind_targeted_work(db, agent_row)
    # Scheduled runs parked while this agent was restarted for them. Separate
    # from the pool binder: those match a pool key, these match the agent a
    # schedule explicitly names.
    await bind_scheduled_work(db, agent_row)


# In-flight CATALOG_REQUEST answers, held so they are not collected mid-flight.
_catalog_answers: set[asyncio.Task] = set()


async def _answer_catalog_request(ws: WebSocket, session_factory, agent_id, payload: dict) -> None:
    from api.services.catalog_requests import answer_catalog_request

    try:
        async with session_factory() as db:
            answer = await answer_catalog_request(db, agent_id, payload)
        frame = Frame(type=FrameType.CATALOG_RESPONSE, payload=answer)
        await ws.send_text(frame.model_dump_json())
    except Exception:
        # The agent gives up on its own timeout and the statement fails with
        # DuckDB's error, so a lost answer costs that statement only.
        logger.exception("Failed to answer a catalog request from agent %s", agent_id)


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
                    # Whatever this agent reported last time may no longer be true
                    # — a static agent can come back re-imaged onto another runtime
                    # — so nothing is routed on it until it reports afresh.
                    "capabilities": None,
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
                            # A restarted instance may be a newer build: the old
                            # instance's report says nothing about this one.
                            capabilities=None,
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
            # A previous run that ended without a disconnect (its replica crashed
            # before the sweeper noticed) is closed at its last proof of life, so
            # the timeline never bridges the outage as uptime.
            await close_unfinished_run(db, agent_id)
            # Recorded for static agents too: "the socket was up and this agent
            # could serve work" is the same fact for both kinds, and it is what the
            # monitoring page's running/not-running timeline is built from.
            await record_lifecycle_event_now(db, agent_id, "connected")
        last_presence_refresh = datetime.now(tz=UTC)
        # Work parked while this agent was starting is bound once it has said what
        # it is — its first AGENT_STATUS, which it sends right after auth_ok — and
        # not at auth_ok itself, when its capabilities are still unknown and every
        # extension and runtime check would be judging nothing.
        reported = False

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
                        if not reported:
                            reported = True
                            await _on_first_report(db, agent_id)

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
                        # The catalog client goes along so lineage extraction can
                        # resolve a source table's columns. It is the process-wide
                        # one the lifespan owns, the same instance every request
                        # handler is given.
                        await handle_agent_frame(
                            db, msg_frame, getattr(ws.app.state, "polaris_client", None)
                        )

                elif msg_frame.type in (FrameType.SESSION_OPENED, FrameType.SESSION_CLOSED):
                    from api.services.sql_sessions.service import handle_session_frame

                    async with session_factory() as db:
                        await handle_session_frame(db, msg_frame)

                elif msg_frame.type == FrameType.STATEMENT_ACK:
                    from api.services.sql_sessions.service import handle_statement_ack

                    async with session_factory() as db:
                        await handle_statement_ack(db, msg_frame)

                elif msg_frame.type == FrameType.CATALOG_REQUEST:
                    # A task, so a cold credential mint for the catalog cannot hold
                    # up this agent's other frames. The answer goes back on this
                    # socket: the request came in on it.
                    task = asyncio.create_task(
                        _answer_catalog_request(ws, session_factory, agent_id, msg_frame.payload)
                    )
                    _catalog_answers.add(task)
                    task.add_done_callback(_catalog_answers.discard)
            except WebSocketDisconnect:
                raise
            except Exception:
                logger.exception("Failed to handle agent frame for agent %s", agent_id)

    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("Agent WebSocket handler failed for agent %s", agent_id)
    finally:
        # A newer socket for this agent has already replaced this one on this
        # replica (a fast reconnect). Its teardown is not an agent going away, so it
        # must not unregister, release, or record anything on the newer socket's
        # behalf.
        superseded = agent_id is not None and registry.get(agent_id) not in (None, ws)
        if agent_id and not superseded:
            registry.unregister(agent_id)
            async with session_factory() as db:
                # Only an agent this replica still owned went away from here; one
                # that already reconnected elsewhere, or that a drain has released
                # and recorded, is not disconnected again.
                if await release_agent_owner(db, agent_id):
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
