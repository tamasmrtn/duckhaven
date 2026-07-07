from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import JSON, BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from api.db.base import Base


class Query(Base):
    __tablename__ = "queries"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    agent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("agents.id"), nullable=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    sql: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="queued")
    # Distinguishes user-initiated queries (null) from internal ones (e.g. "sample"),
    # so synthetic preview queries can be excluded from history/audit.
    origin: Mapped[str | None] = mapped_column(String(50), nullable=True)
    row_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Size of the materialized Parquet result file; null for DDL/DML (no result).
    result_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    progress: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Normalized post-execution DuckDB profile (query summary + operator tree);
    # null for DDL/DML, older queries, or when profiling failed. KB-sized. JSONB
    # on Postgres; plain JSON on other dialects (e.g. SQLite under tests).
    profile: Mapped[dict | None] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"), nullable=True
    )
    result_path: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    # Set when this run was produced by a schedule (origin="scheduled"); null for
    # interactive runs. Powers the per-schedule run-history list.
    schedule_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("schedules.id", ondelete="SET NULL"), nullable=True
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    workspace: Mapped[Workspace] = relationship(back_populates="queries")


class SavedQuery(Base):
    __tablename__ = "saved_queries"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    sql: Mapped[str] = mapped_column(Text, nullable=False)
    default_agent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agents.id"), nullable=True
    )
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    workspace: Mapped[Workspace] = relationship(back_populates="saved_queries")


class Schedule(Base):
    """A time-based trigger that runs a job unattended on a cron cadence.

    Generic by design: ``job_type`` discriminates what kind of work runs, and the
    scheduler dispatches each type through a small seam. v1 implements only
    ``"saved_query"`` (run a saved query's SQL verbatim); future job types (e.g.
    ``dbt``/``dlt``) add their own target column(s) without touching the cron loop,
    leader election, or run recording.
    """

    __tablename__ = "schedules"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    # Discriminator + extension seam. v1 only emits/handles "saved_query".
    job_type: Mapped[str] = mapped_column(String(50), nullable=False, default="saved_query")
    # Target for saved_query jobs (the only concrete job type in v1). CASCADE so
    # deleting a saved query removes its schedule.
    saved_query_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("saved_queries.id", ondelete="CASCADE"), nullable=True
    )
    # User-chosen agent. Null => fall back to the saved query's default_agent_id,
    # then to pick_agent_for. Connectivity is checked at dispatch (fail fast).
    agent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agents.id", ondelete="SET NULL"), nullable=True
    )
    cron: Mapped[str] = mapped_column(String(255), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Most recent dispatched run: drives skip-if-running and surfaces last-run
    # status without a join over the queries table. A soft pointer (no FK) to
    # avoid a circular schedules<->queries dependency at table-create time.
    last_run_query_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
