"""One workspace's replay of its own query history through lineage extraction.

DuckHaven keeps every statement's SQL text indefinitely, so a graph that starts
empty is empty only because nobody has read the history yet. This row is the
state of doing that reading: what was asked for, how far it has got, and what it
found.

A durable row rather than a request that runs to completion, for the same reasons
:class:`~api.models.catalog_migration.CatalogMigration` is one — the work is
unbounded in principle, has to survive a replica restart, and is worth watching
while it runs. The runner claims it, walks a batch, commits, and comes back; the
cursor is what makes that safe to interrupt anywhere.

One row per workspace, reused across runs. That is deliberate: the row is not a
log of attempts but a **record of which history has been read**, and that record
is what makes a second backfill a no-op instead of a second pass that inflates
every observation count.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from api.db.base import Base

# Application-managed, like `Query.status` and `CatalogMigration.status`: no DB
# enum, so adding a state later is not a migration.
ACTIVE_STATUSES = ("pending", "running")
TERMINAL_STATUSES = ("completed", "failed", "cancelled")


class LineageBackfill(Base):
    """The state of one workspace's lineage backfill."""

    __tablename__ = "lineage_backfills"
    __table_args__ = (
        # One per workspace. A second request adjusts this row rather than racing
        # a duplicate walk over the same history.
        UniqueConstraint("workspace_id", name="uq_lineage_backfills_workspace"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    # A rehearsal: everything is derived and persisted exactly as in a real run,
    # then rolled back. Only the counters survive.
    dry_run: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # The oldest statement this run was asked to reach. NULL means all history.
    since_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # The window of history already read, across every run so far. `covered_from`
    # walks backwards as an operator asks for more; `covered_through` is where
    # live extraction takes over. Together they are what makes running the
    # backfill twice cost nothing and change nothing.
    covered_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    covered_through: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Resume point within the current run. `(started_at, id)` is a total order
    # over `queries`, so a batch boundary is a place the walk can be interrupted
    # and picked up again without re-reading or skipping a statement.
    cursor_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    cursor_query_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)

    # What the run has seen. Kept as separate counters rather than one "done"
    # number because the interesting question after a backfill is which of these
    # is unexpectedly large.
    queries_scanned: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    queries_with_lineage: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    queries_skipped: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    parse_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    queries_failed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    edges_created: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    edges_updated: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Cooperative, checked between batches — the same shape as a catalog
    # migration's cancel, and for the same reason: killing a walk mid-batch would
    # leave the cursor lying about where it got to.
    cancel_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    requested_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
