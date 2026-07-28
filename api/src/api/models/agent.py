import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, Float, String, func
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

    # ── Elastic-agent lifecycle (all NULL for a static, operator-run agent) ──────
    # Which compute backend provisioned this agent. NULL = static (unchanged
    # behaviour); "null" = the no-op test backend; "azure_aci" = Azure Container
    # Instances. Also the discriminator that routes terminate/status/list_managed.
    provider: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # The backend's own handle for the provisioned instance (e.g. an ACI container
    # group name). Set once provision returns; used for terminate + leak detection.
    instance_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # The cloud-instance lifecycle, orthogonal to `status`/`last_ping_at` (which
    # track socket presence): provisioning -> running -> terminating ->
    # terminated / failed. The idle reaper drives the scale-in transitions.
    lifecycle: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # Capability/backend scope this agent serves. Demand (a workspace's backend
    # kinds) is matched to supply by this key so one pool serves many workspaces.
    pool_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Last time work was dispatched to this agent; drives idle scale-in. Distinct
    # from last_ping_at (heartbeats, which never stop) — this only advances on work.
    last_active_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # The size this elastic agent was provisioned at (vCPU + GiB). Persisted so the
    # admin UI can show the agent's size and hourly cost immediately — before it
    # dials home and advertises its real capabilities. NULL for static agents.
    requested_cpu: Mapped[float | None] = mapped_column(Float, nullable=True)
    requested_memory_gb: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Per-agent idle timeout (seconds) before the reaper scales it in. NULL falls
    # back to the global ``elastic_idle_timeout_s``. Set at create time so each
    # elastic agent can auto-terminate on its own schedule (like a cluster).
    idle_timeout_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    provisioned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    terminated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
