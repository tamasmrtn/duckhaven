from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from api.db.base import Base


class StorageBackend(Base):
    __tablename__ = "storage_backends"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    kind: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    root_uri: Mapped[str] = mapped_column(String(2048), nullable=False)
    uc_storage_credential_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Nullable for the system catalog's backend, which is DuckHaven-owned and may
    # be provisioned at startup before any user exists.
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    catalogs: Mapped[list[Catalog]] = relationship(back_populates="storage_backend")
