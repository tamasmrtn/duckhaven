import asyncio
import logging
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime

from fastapi import WebSocket

logger = logging.getLogger(__name__)

# ~5 minutes of live samples at the agent's 2s cadence. In-memory only; lost on
# restart, which is acceptable for a real-time utilization view.
_METRICS_WINDOW = 150


@dataclass
class AgentConnection:
    agent_id: uuid.UUID
    ws: WebSocket
    last_ping_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    metrics: deque[dict] = field(default_factory=lambda: deque(maxlen=_METRICS_WINDOW))
    # One socket is shared by every concurrent request handler that dispatches to
    # this agent. Serializes sends so two frames can never interleave on the wire.
    send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class ConnectionManager:
    def __init__(self) -> None:
        self._connections: dict[str, AgentConnection] = {}

    def register(self, agent_id: uuid.UUID, ws: WebSocket) -> None:
        self._connections[str(agent_id)] = AgentConnection(agent_id=agent_id, ws=ws)

    def unregister(self, agent_id: uuid.UUID) -> None:
        self._connections.pop(str(agent_id), None)

    def get(self, agent_id: uuid.UUID) -> WebSocket | None:
        conn = self._connections.get(str(agent_id))
        return conn.ws if conn else None

    def touch(self, agent_id: uuid.UUID) -> None:
        conn = self._connections.get(str(agent_id))
        if conn:
            conn.last_ping_at = datetime.now(UTC)

    def connected_ids(self) -> set[str]:
        return set(self._connections.keys())

    def record_metrics(self, agent_id: uuid.UUID, sample: dict) -> None:
        conn = self._connections.get(str(agent_id))
        if conn:
            conn.metrics.append(sample)

    def recent_metrics(self) -> dict[str, list[dict]]:
        return {aid: list(conn.metrics) for aid, conn in self._connections.items()}

    async def send(self, agent_id: uuid.UUID, payload: str) -> bool:
        conn = self._connections.get(str(agent_id))
        if conn is None:
            return False
        try:
            async with conn.send_lock:
                await conn.ws.send_text(payload)
        except Exception:
            self.unregister(agent_id)
            return False
        # Post-send log: the send-side half of the frame trail. Together with the
        # agent's receive log this localizes a lost frame to one side of the wire.
        logger.info("Frame sent to agent %s: %s", agent_id, payload[:200])
        return True

    async def close(self, agent_id: uuid.UUID, code: int = 1012) -> bool:
        """Close a locally-held socket (1012 = Service Restart) so the agent
        reconnects elsewhere. Used by force-disconnect and graceful drain."""
        conn = self._connections.get(str(agent_id))
        if conn is None:
            return False
        try:
            await conn.ws.close(code=code)
        except Exception:
            pass
        self.unregister(agent_id)
        return True


registry = ConnectionManager()
