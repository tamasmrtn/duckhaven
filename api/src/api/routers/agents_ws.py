import json
import secrets
import uuid
from datetime import UTC, datetime

import sqlalchemy as sa
from duckhaven_shared.protocol import Frame, FrameType
from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_db
from api.models.agent import Agent
from api.models.user import Credential
from api.services.agent_registry import registry

router = APIRouter()


@router.websocket("/agents/connect")
async def agent_connect(ws: WebSocket, db: AsyncSession = Depends(get_db)) -> None:
    await ws.accept()
    agent_id: uuid.UUID | None = None

    try:
        raw = await ws.receive_text()
        frame = Frame.model_validate_json(raw)
        if frame.type != "auth" or "token" not in frame.payload:
            await ws.close(code=1008, reason="Expected auth frame")
            return

        token = frame.payload["token"]
        result = await db.execute(
            select(Credential).where(
                Credential.token == token,
                Credential.kind == "agent_bootstrap",
            )
        )
        cred = result.scalar_one_or_none()
        if cred is None:
            await ws.close(code=1008, reason="Invalid bootstrap token")
            return

        await db.delete(cred)

        label = frame.payload.get("name", f"agent-{secrets.token_hex(4)}")
        agent = Agent(name=label, status="healthy")
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
            msg_frame = Frame.model_validate_json(raw_msg)

            if msg_frame.type == FrameType.HEARTBEAT:
                registry.touch(agent_id)
                await ws.send_text(Frame(type=FrameType.HEARTBEAT).model_dump_json())

            elif msg_frame.type == FrameType.AGENT_STATUS:
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

            elif msg_frame.type in (FrameType.QUERY_DONE, FrameType.QUERY_PROGRESS):
                from api.services.query import handle_agent_frame

                await handle_agent_frame(db, msg_frame)

    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        if agent_id:
            registry.unregister(agent_id)
            await db.execute(
                sa.update(Agent).where(Agent.id == agent_id).values(status="unavailable")
            )
            await db.commit()
