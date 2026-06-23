from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from api.db.base import Base


class SystemCatalogSync(Base):
    """Singleton high-water mark for the system-catalog materializer.

    The materializer copies terminal ``queries`` rows into the Iceberg
    ``duckhaven.query.history``/``access.audit`` tables incrementally; this row
    records the ``(started_at, id)`` of the last query it copied so the next
    cycle only reads newer rows. A null cursor means "materialize everything"
    (the first-run backfill).
    """

    __tablename__ = "system_catalog_sync"

    # Fixed singleton id so upserts never create a second row.
    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    query_cursor_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    query_cursor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Last error string from a failed cycle (cleared on success), for ops.
    last_error: Mapped[str | None] = mapped_column(String(2048), nullable=True)
