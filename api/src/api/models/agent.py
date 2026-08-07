import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Index, Integer, String, func
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
    # Whether this agent's per-agent ACL (:class:`AgentGrant`) is consulted for the
    # `use` tier. "open" (the default) means any authenticated caller may target it,
    # exactly as before there was an ACL; "restricted" means `use` needs an explicit
    # grant. Higher tiers always need a grant or global `agents:manage` in either
    # mode. Mirrors ``workspace_catalogs.access_mode`` (open/scoped).
    access_mode: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="open", default="open"
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
    # Per-agent query timeout ceiling (seconds), passed to the instance as
    # MAX_TIMEOUT_S so a long-running analytical query on this agent isn't cut off
    # at the code default (600s). NULL falls back to the agent image's own default.
    # Persisted so a restart re-provisions with the same ceiling, like requested_cpu.
    requested_max_timeout_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    provisioned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    terminated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# The transitions an agent can record. Presence events (connected/disconnected)
# apply to every agent; the rest only to provisioned ones.
LIFECYCLE_EVENTS = (
    "provisioning",
    "connected",
    "disconnected",
    "terminating",
    "terminated",
    "failed",
)


class AgentLifecycleEvent(Base):
    """One append-only record of an agent changing state.

    The ``agents`` row is mutated in place — a restart reuses it — so it only ever
    describes the agent *now*. This is the history: what the agent was doing, and
    when, across every run. It is the only source for the monitoring page's
    running/not-running timeline.

    ``connected``/``disconnected`` are written for static agents too, so that
    timeline means the same thing for both kinds: the agent's socket was up and it
    could serve work. Elastic agents additionally record the provisioning and
    teardown transitions around that.

    ``reason`` explains a transition that wasn't user-initiated, using the same
    vocabulary the reaper already counts by (``idle``, ``max_lifetime``,
    ``provisioning_timeout``, ``orphan``, ``dead_row``) so the UI and the
    ``duckhaven_agent_reaped`` Prometheus counter can never disagree about what
    happened. NULL when the event needs no explanation (a plain connect).
    """

    __tablename__ = "agent_lifecycle_event"
    __table_args__ = (Index("ix_agent_lifecycle_event_agent_at", "agent_id", "at"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agents.id", ondelete="CASCADE"), nullable=False
    )
    event: Mapped[str] = mapped_column(String(32), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AgentMetricsMinute(Base):
    """One minute of an agent's resource and queue telemetry, rolled up.

    Agents sample themselves every ~2s, but those samples only ever lived in a
    150-entry in-memory ring buffer (``services.agent_registry``) — about five
    minutes, per replica, lost on restart. This is the durable form, aggregated to
    one row per agent per minute: ~1.4k rows/agent/day instead of the ~43k that
    persisting raw samples would cost, and enough resolution for the 1–24h windows
    the monitoring page offers.

    Both an average and a max are kept for each resource, because they answer
    different questions: the average is what the agent cost you, the max is what
    made a query slow. Queue depths keep only the max — a peak of 1 queued query
    matters, an average of 0.3 does not.

    ``running_max``/``queued_max`` are the agent's *own* admission-queue depth. The
    control plane cannot derive them: a query parked in the agent's executor deque
    is queued in a way no control-plane timestamp records.
    """

    __tablename__ = "agent_metrics_minute"

    agent_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agents.id", ondelete="CASCADE"), primary_key=True
    )
    # Truncated to the minute; with agent_id it is the natural key an in-flight
    # accumulator upserts onto when it flushes.
    minute: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    cpu_avg: Mapped[float] = mapped_column(Float, nullable=False)
    cpu_max: Mapped[float] = mapped_column(Float, nullable=False)
    mem_avg: Mapped[float] = mapped_column(Float, nullable=False)
    mem_max: Mapped[float] = mapped_column(Float, nullable=False)
    running_max: Mapped[int] = mapped_column(Integer, nullable=False)
    queued_max: Mapped[int] = mapped_column(Integer, nullable=False)
    session_max: Mapped[int] = mapped_column(Integer, nullable=False)
    # How many samples the averages are over. Kept so a second replica flushing the
    # same minute after an ownership handoff can merge a weighted mean rather than
    # overwrite one partial minute with another.
    sample_count: Mapped[int] = mapped_column(Integer, nullable=False)
