from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from api.db.base import Base


class ResultCacheEntry(Base):
    """A finished query's result, kept so the same query can be answered again
    without running it while nothing it read has changed.

    One entry per query identity (``key_hash``, see `services/result_cache/key.py`)
    in a workspace. ``tables`` records the version of every table the result was
    computed from; a lookup serves the entry only while each is unchanged, or
    proven changed in a way that left the data alone.

    The rows themselves are either copied here (``storage="inline"``, small
    results: served with no agent involved) or left in the Parquet file on the
    agent that ran the query (``storage="agent"``), which keeps the file past its
    normal retention while the entry exists.
    """

    __tablename__ = "result_cache_entries"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    # The query as written by whoever first ran it, for operators reading the table.
    sql: Mapped[str] = mapped_column(Text, nullable=False)
    # The binding context the key covers: catalog, schema, runtime and time zone.
    context: Mapped[dict] = mapped_column(JSON().with_variant(JSONB, "postgresql"), nullable=False)
    # `[{catalog_id, schema, table, content_id, version_token}]`, plus one entry per
    # catalog whose stored functions could change a result (`table` null).
    tables: Mapped[list] = mapped_column(JSON().with_variant(JSONB, "postgresql"), nullable=False)
    # The run that produced the result. Deleting it deletes the entry: the agent
    # file is addressed by this id.
    source_query_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("queries.id", ondelete="CASCADE"), nullable=False
    )
    storage: Mapped[str] = mapped_column(String(16), nullable=False)
    agent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agents.id", ondelete="SET NULL"), nullable=True
    )
    inline_parquet: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    result_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    row_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    result_schema: Mapped[list | None] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"), nullable=True
    )
    runtime_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # What producing the result cost, so eviction keeps what is expensive to redo.
    compute_ms: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    hit_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    last_hit_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    # Moved forward on every hit; `hard_expires_at` never moves.
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    hard_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint("workspace_id", "key_hash", name="uq_result_cache_workspace_key"),
        Index("ix_result_cache_expires_at", "expires_at"),
        Index("ix_result_cache_source_query", "source_query_id"),
    )
