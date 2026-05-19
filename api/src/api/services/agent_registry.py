import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

from fastapi import WebSocket


@dataclass
class AgentConnection:
    agent_id: uuid.UUID
    ws: WebSocket
    last_ping_at: datetime = field(default_factory=lambda: datetime.now(UTC))


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
            conn.last_ping_at = datetime.utcnow()

    def connected_ids(self) -> set[str]:
        return set(self._connections.keys())

    async def send(self, agent_id: uuid.UUID, payload: str) -> bool:
        ws = self.get(agent_id)
        if ws is None:
            return False
        try:
            await ws.send_text(payload)
            return True
        except Exception:
            self.unregister(agent_id)
            return False


registry = ConnectionManager()
