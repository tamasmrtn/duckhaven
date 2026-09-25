from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from api.db.base import Base


class CatalogExport(Base):
    """One run of copying a DuckLake catalog into a new Iceberg catalog.

    Deliberately not a CatalogMigration: an active migration freezes writes,
    and an export reads the latest snapshot so it need not. No event table;
    the Query row is both progress handle and audit record.
    """

    __tablename__ = "catalog_exports"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_catalog_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("catalogs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Null until `pending` creates it.
    target_catalog_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("catalogs.id"), nullable=True
    )
    target_name: Mapped[str] = mapped_column(String(255), nullable=False)
    target_storage_backend_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("storage_backends.id"), nullable=False
    )
    # The target is attached here so both catalogs share one agent connection.
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    # The COPY dispatch.
    query_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    tables_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tables_done: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
