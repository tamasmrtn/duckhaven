"""The lineage graph: which datasets were produced from which other datasets.

A :class:`LineageEdge` is a single ``source -> target`` relationship, meaning
"data in the target was produced using the source". Edges are stored denormalized
— each row carries both endpoints' names directly — for the same reason
``catalog_grants`` and ``table_metadata`` do: Polaris owns catalog structure and
the control plane never caches it (I3). There is deliberately no asset table.

Edges are keyed by *catalog*, not workspace, mirroring
:class:`~api.models.table_metadata.TableMetadata`'s rationale: a relationship
between two tables is an intrinsic fact about the data, so a catalog attached to
both ``dev`` and ``prod`` has one lineage graph rather than two. ``workspace_id``
records where the relationship was *observed* and is provenance only — the read
boundary is enforced at query time by clamping traversal to the catalogs the
requesting workspace attaches.

Every edge names the ``provider`` that asserted it, and the provider is part of
the identity key. Two producers describing the same pair therefore coexist as two
rows rather than overwriting each other, and reconciliation is always scoped to a
single provider. Disagreement between producers is information, not a conflict to
resolve; the read API merges the rows into one graph edge listing both.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from api.db.base import Base


class LineageEdge(Base):
    """One ``source -> target`` dataset relationship, as asserted by one provider.

    The ``*_key`` columns are the canonical asset key (see
    ``api.services.lineage.keys``): a single indexed string per endpoint, built on
    ``catalogs.id`` rather than the catalog slug so renaming a catalog does not
    orphan its lineage. The structured ``*_catalog_id``/``*_schema``/``*_table``
    columns are kept alongside for display and for cascade cleanup, not as the
    join key.

    An endpoint with ``catalog_id IS NULL`` is *external* — something outside any
    DuckHaven catalog, named by the producer (``system``). That is how an imported
    graph keeps its roots instead of silently losing them.
    """

    __tablename__ = "lineage_edges"
    __table_args__ = (
        # The dedup key. `provider` is part of it on purpose: it is what lets
        # execution-derived and imported lineage assert the same pair without
        # either one clobbering the other.
        UniqueConstraint("provider", "source_key", "target_key", name="uq_lineage_edges_identity"),
        Index("ix_lineage_edges_source_key", "source_key"),
        Index("ix_lineage_edges_target_key", "target_key"),
        Index("ix_lineage_edges_provider_run", "provider", "provider_run_id"),
        # Cleanup when a table or schema is dropped.
        Index(
            "ix_lineage_edges_source_catalog",
            "source_catalog_id",
            "source_schema",
            "source_table",
        ),
        Index(
            "ix_lineage_edges_target_catalog",
            "target_catalog_id",
            "target_schema",
            "target_table",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    source_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    source_catalog_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("catalogs.id", ondelete="CASCADE"), nullable=True
    )
    # Set exactly when `source_catalog_id` is NULL: the external system's name.
    source_system: Mapped[str | None] = mapped_column(String(128), nullable=True)
    source_schema: Mapped[str] = mapped_column(String(255), nullable=False)
    source_table: Mapped[str] = mapped_column(String(255), nullable=False)

    target_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    target_catalog_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("catalogs.id", ondelete="CASCADE"), nullable=True
    )
    target_system: Mapped[str | None] = mapped_column(String(128), nullable=True)
    target_schema: Mapped[str] = mapped_column(String(255), nullable=False)
    target_table: Mapped[str] = mapped_column(String(255), nullable=False)

    # Who asserted this relationship: "execution" for lineage derived from SQL
    # DuckHaven ran, or an importer's name (e.g. "dbt").
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    # The import batch this edge last arrived in — the unit of reconciliation.
    # Null for execution-derived edges, which are never batch-reconciled.
    provider_run_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Where the relationship was observed. Provenance only: visibility is decided
    # by which catalogs the *requesting* workspace attaches, not by this column.
    workspace_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workspaces.id", ondelete="SET NULL"), nullable=True
    )
    # What kind of statement produced it: create_table_as, create_view, insert,
    # update, merge, delete — or an importer's own vocabulary (dbt: "model").
    operation: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # "exact" when parsed or declared; "inferred" is reserved for future heuristic
    # producers. Nothing emits "inferred" today — the column exists so uncertainty
    # has somewhere to live rather than being laundered into fact.
    confidence: Mapped[str] = mapped_column(String(16), nullable=False, default="exact")
    # Whether this provider worked out the column-level detail, and not merely
    # whether any :class:`LineageColumnEdge` rows exist. "derived" with no children
    # is a real and useful answer — the source was joined against or filtered on but
    # none of its values reached the target — and it must not read as "we don't
    # know", which is what "unknown" (never attempted) and "unsupported" (attempted,
    # could not) say.
    column_lineage: Mapped[str] = mapped_column(
        String(16), nullable=False, default="unknown", server_default="unknown"
    )

    # The query that most recently produced this edge, for click-through to the
    # exact SQL. A soft pointer with no FK (like `schedules.last_run_query_id`) so
    # trimming query history never cascades into the graph.
    last_query_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)

    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    # How many times this relationship has been re-asserted. Together with the
    # two timestamps this answers "when did this start / last happen / how often"
    # without an event log — the history itself stays recoverable from `queries`,
    # which retains every statement's SQL text indefinitely.
    observation_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class LineageColumnEdge(Base):
    """A column-level refinement of a :class:`LineageEdge`.

    Deliberately a child of the dataset edge rather than an independent
    relationship: a column edge cannot exist without the dataset edge it refines,
    so the table graph is always a correct coarsening of the column graph. Hanging
    off ``edge_id`` also means these inherit the parent's provenance for free —
    which provider asserted the mapping is whichever one owns the parent row — and
    that a table rename, which rewrites the parent in place, carries them along
    without touching a single child.

    Re-observation *accumulates*. Two statements can legitimately build the same
    target from the same source through different columns, so the union is the
    truth and deleting whatever the newest statement did not mention would make a
    mapping flap in and out. A mapping nothing re-asserts ages out through
    ``last_seen_at`` instead, exactly as the parent edge does.
    """

    __tablename__ = "lineage_column_edges"
    __table_args__ = (
        UniqueConstraint(
            "edge_id", "source_column", "target_column", name="uq_lineage_column_edges_identity"
        ),
        Index("ix_lineage_column_edges_edge", "edge_id"),
        # "What feeds this column?" The unique constraint above already indexes
        # the (edge_id, source_column) prefix for the other direction.
        Index("ix_lineage_column_edges_edge_target", "edge_id", "target_column"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    edge_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("lineage_edges.id", ondelete="CASCADE"), nullable=False
    )
    source_column: Mapped[str] = mapped_column(String(255), nullable=False)
    target_column: Mapped[str] = mapped_column(String(255), nullable=False)

    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
