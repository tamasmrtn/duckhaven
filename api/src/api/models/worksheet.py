from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from api.db.base import Base


class Worksheet(Base):
    """A user's private, autosaved SQL editor tab.

    Distinct from `SavedQuery`, which is the shared, named, schedulable artifact:
    a worksheet may link to one through `saved_query_id`, and "Save" copies its SQL
    there. `version` counts content edits (SQL or title) so two windows editing the
    same worksheet cannot silently overwrite each other.
    """

    __tablename__ = "worksheets"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False, default="Untitled")
    sql: Mapped[str] = mapped_column(Text, nullable=False, default="")
    agent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agents.id", ondelete="SET NULL"), nullable=True
    )
    # Catalog slug USEd for unqualified names; no FK because a catalog can be
    # detached while a worksheet still names it.
    catalog: Mapped[str | None] = mapped_column(String(255), nullable=True)
    timeout_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    saved_query_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("saved_queries.id", ondelete="SET NULL"), nullable=True
    )
    # Soft pointer to the last run, so a reload restores its results. No FK, like
    # `Schedule.last_run_query_id`: a pruned query just means "no results".
    last_query_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    is_open: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    tab_position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    # Moves only when content changes, so "recently edited" ignores tab shuffling.
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index("ix_worksheets_owner_ws_open", "owner_id", "workspace_id", "is_open"),
        Index("ix_worksheets_owner_ws_updated", "owner_id", "workspace_id", "updated_at"),
        Index("ix_worksheets_saved_query_id", "saved_query_id"),
    )
