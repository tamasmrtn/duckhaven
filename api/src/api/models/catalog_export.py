from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from api.db.base import Base


class CatalogExport(Base):
    """One run of copying a DuckLake catalog into a new Iceberg catalog.

    DuckHaven tells users at the moment of choice that a DuckLake table is
    readable by DuckDB and nothing else. That stays true of the catalog itself,
    but it need not be the end of the story: ``COPY FROM DATABASE`` writes the
    whole thing into an Iceberg catalog that Spark, Trino and PyIceberg can open.
    This records one such run.

    **Deliberately not a CatalogMigration.** They look alike, and reusing it
    would be wrong for a specific reason: ``workspace_has_active_migration`` is
    the write-freeze, so an export would stop writes to the source. It must not
    — an export reads the latest snapshot, and a concurrent write simply is not
    included.

    No event table. There are four state transitions and one Query row, and the
    Query is both the progress handle and the audit record.
    """

    __tablename__ = "catalog_exports"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_catalog_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("catalogs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Null until `pending` creates it; a failed create leaves nothing behind.
    target_catalog_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("catalogs.id"), nullable=True
    )
    target_name: Mapped[str] = mapped_column(String(255), nullable=False)
    target_storage_backend_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("storage_backends.id"), nullable=False
    )
    # Where it dispatches, and where the target is attached so both catalogs
    # land on one agent connection.
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    # The COPY dispatch. The query log is this codebase's audit trail, so this
    # is the pointer to who ran it and what it did.
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
