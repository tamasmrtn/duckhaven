from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from api.db.base import Base


class CatalogMigration(Base):
    """One run of moving a catalog's Iceberg data from one storage backend to
    another, preserving all snapshots and Polaris references.

    Data files + metadata are physically copied to a *shadow* Polaris catalog at
    the target backend (with every absolute path rewritten), then the catalog is
    atomically re-pointed at the shadow in a single transaction. The catalog's
    ``storage_backend_id`` / ``polaris_name`` change *only* in that cutover, so a
    failure before cutover always leaves the catalog on the old backend.

    ``status`` is an application-managed string (no DB enum), mirroring
    ``Query.status``: ``pending`` → ``copying`` → ``verifying`` → ``cutover`` →
    ``completed``, with ``failed`` / ``cancelled`` as the other terminal states.
    """

    __tablename__ = "catalog_migrations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    catalog_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("catalogs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_storage_backend_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("storage_backends.id"), nullable=False
    )
    target_storage_backend_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("storage_backends.id"), nullable=False
    )
    # The fresh Polaris catalog provisioned at the target backend; null until the
    # runner provisions it. The catalog's polaris_name is set to this at cutover.
    shadow_polaris_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # The catalog's polaris_name at start, captured before cutover overwrites it.
    # Needed to drop the retained old catalog later and to support rollback.
    source_polaris_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    tables_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tables_done: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    bytes_total: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    bytes_copied: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    # Cooperative cancel flag the engine checks between tables; a cancel before
    # cutover stops the run and tears the shadow catalog down.
    cancel_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cutover_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    catalog: Mapped[Catalog] = relationship()
    tables: Mapped[list[CatalogMigrationTable]] = relationship(
        back_populates="migration", cascade="all, delete-orphan"
    )
    events: Mapped[list[CatalogMigrationEvent]] = relationship(
        back_populates="migration", cascade="all, delete-orphan"
    )


class CatalogMigrationTable(Base):
    """Per-table checkpoint for a migration, so a crash mid-copy resumes from the
    last completed table instead of restarting. ``status`` advances
    ``pending`` → ``copied`` → ``registered`` → ``verified`` (or ``failed``)."""

    __tablename__ = "catalog_migration_tables"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    migration_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("catalog_migrations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    schema_name: Mapped[str] = mapped_column(String(255), nullable=False)
    table_name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    source_metadata_location: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    target_metadata_location: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    bytes_copied: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    migration: Mapped[CatalogMigration] = relationship(back_populates="tables")


class CatalogMigrationEvent(Base):
    """A human-readable log line for a migration, streamed to the admin UI.

    Distinct from the server-side Python ``logger`` output: these rows are the
    operator-facing narrative the migration log viewer polls. ``seq`` is a
    monotonic per-migration counter used as the incremental ``?after=`` cursor.
    """

    __tablename__ = "catalog_migration_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    migration_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("catalog_migrations.id", ondelete="CASCADE"), nullable=False
    )
    seq: Mapped[int] = mapped_column(BigInteger, nullable=False)
    level: Mapped[str] = mapped_column(String(10), nullable=False, default="info")
    message: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    migration: Mapped[CatalogMigration] = relationship(back_populates="events")
