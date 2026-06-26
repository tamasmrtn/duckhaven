from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from api.db.base import Base


class TableMetadata(Base):
    """Control-plane-tracked catalog metadata that Polaris does not provide
    (ownership, last-write provenance, row/size stats). These are intrinsic
    table facts, so they are keyed by the catalog (not the workspace): a catalog
    shared across workspaces has one owner/row-count row. Populated on table
    create and on write/sample completion.
    """

    __tablename__ = "table_metadata"
    __table_args__ = (
        UniqueConstraint("catalog_id", "schema_name", "table_name", name="uq_table_metadata_ident"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    catalog_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("catalogs.id"), nullable=False)
    schema_name: Mapped[str] = mapped_column(String(255), nullable=False)
    table_name: Mapped[str] = mapped_column(String(255), nullable=False)

    owner_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    row_count: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    # Iceberg-native metadata, refreshed from the agent probe on table sample.
    snapshot_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    snapshot_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    data_file_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    has_deletes: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    last_write_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_write_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    last_write_agent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agents.id"), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
