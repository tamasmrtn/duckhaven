from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from api.db.base import Base

# JSONB on Postgres, plain JSON elsewhere (SQLite under unit tests).
_Json = JSON().with_variant(JSONB, "postgresql")


class StorageBackend(Base):
    __tablename__ = "storage_backends"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    kind: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    root_uri: Mapped[str] = mapped_column(String(2048), nullable=False)
    # Kind-specific credential config for external backends (S3 role ARN /
    # external id / region; ADLS tenant id / app / consent). NULL for the
    # bundled object_store. Holds only identifiers — never a static secret.
    config: Mapped[dict | None] = mapped_column(_Json, nullable=True)
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    catalogs: Mapped[list[Catalog]] = relationship(back_populates="storage_backend")
