from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from api.db.base import Base


class SqlSession(Base):
    """A SQL session: a persistent, agent-held DuckDB connection the control plane
    brokers on behalf of a client (dbt/dlt via the future connector).

    The session is **state-of-record in Postgres** (I9); the agent's held
    connection is ephemeral socket state. It pins one ``agent_id`` — every
    statement routes there — and carries the workspace/principal for per-statement
    authorization. Statements themselves are ordinary ``queries`` rows tagged with
    ``session_id`` (``origin="session"``), so the existing poll/fetch/audit path is
    reused. ``last_active_at`` drives idle reaping; ``created_at`` the max-lifetime
    cap. ``staging_uri`` is the scoped object-storage prefix a load may ``COPY``
    to/from (the statement policy's allow-prefix).
    """

    __tablename__ = "sql_sessions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    # The pinned agent holding this session's connection. SET NULL if the agent
    # row is deleted; the reaper/reconciler fails the session in that case.
    agent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agents.id", ondelete="SET NULL"), nullable=True
    )
    # Requesting principal (null for system-originated sessions); drives
    # per-statement grant checks (grants.assert_query_access).
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    # opening -> open -> closing -> closed; or failed/expired terminal states.
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="opening")
    # Why the session ended, as a typed value: client / idle / max_lifetime /
    # open_timeout / agent_disconnect / agent_lease / failed. Mirrors the taxonomy
    # `record_sql_session_closed` meters, so the audit UI never has to parse the
    # free-text `error`. Null while the session lives, and on rows that ended
    # before this column existed.
    close_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # Which tool opened the session, parsed from the request's `User-Agent`
    # (`dbt-duckhaven/1.2.0`). Postgres' `application_name` analog; null when the
    # header is absent or unparseable.
    client_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    client_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    active_catalog: Mapped[str | None] = mapped_column(String(255), nullable=True)
    staging_uri: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Refreshed on every statement; the idle-reaper compares it to now().
    last_active_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
