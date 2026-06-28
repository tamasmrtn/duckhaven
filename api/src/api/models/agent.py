import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from api.db.base import Base


class Agent(Base):
    __tablename__ = "agents"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="unavailable")
    capabilities: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    result_host: Mapped[str | None] = mapped_column(String(255), nullable=True)
    result_port: Mapped[int | None] = mapped_column(nullable=True)
    last_ping_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Which API replica currently holds this agent's WebSocket, and the internal
    # URL peers use to forward dispatch frames to it. Both NULL when the agent is
    # not connected anywhere. Set on registration, cleared on disconnect.
    owner_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    owner_url: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
