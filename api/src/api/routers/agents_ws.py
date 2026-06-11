import json
import logging
import secrets
import uuid
from datetime import UTC, datetime

import sqlalchemy as sa
from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from api.deps import get_session_factory
from api.models.agent import Agent
from api.models.user import Credential
from api.services.agent_registry import registry
from duckhaven_shared.protocol import Frame, FrameType

logger = logging.getLogger(__name__)

router = APIRouter()


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
        # The agent's result server is reachable at the socket peer address; the
        # agent advertises its result port in the auth frame. Together these tell
        # services/query.proxy_rows where to fetch result Parquet.
        result_host = ws.client.host if ws.client else None
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

            if cred.kind == "agent_session":
                # Re-authentication: rebind the existing agent row instead of
                # minting a new one. The session token is long-lived and is not
                # consumed.
                agent_id = cred.agent_id
                await db.execute(
                    sa.update(Agent)
                    .where(Agent.id == agent_id)
                    .values(status="healthy", result_host=result_host, result_port=result_port_int)
                )
                session_token = token
                await db.commit()
            else:
                # First registration: the bootstrap token is single-use.
                await db.delete(cred)
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

        async for raw_msg in ws.iter_text():
            # Each frame is isolated: a per-frame session keeps no pooled
            # connection between frames, and a failed write can't poison the next
            # frame. A frame whose handling raises is logged and skipped rather
            # than tearing down the socket.
            try:
                msg_frame = Frame.model_validate_json(raw_msg)

                if msg_frame.type == FrameType.HEARTBEAT:
                    registry.touch(agent_id)
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
                    # High-frequency live utilization: kept in an in-memory ring
                    # buffer only, never persisted.
                    registry.record_metrics(agent_id, msg_frame.payload)

                elif msg_frame.type in (FrameType.QUERY_DONE, FrameType.QUERY_PROGRESS):
                    from api.services.query import handle_agent_frame

                    async with session_factory() as db:
                        await handle_agent_frame(db, msg_frame)
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
                await db.execute(
                    sa.update(Agent).where(Agent.id == agent_id).values(status="unavailable")
                )
                await db.commit()
