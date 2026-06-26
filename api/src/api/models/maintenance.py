from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from api.db.base import Base

# JSONB on Postgres, plain JSON elsewhere (SQLite under unit tests).
_Json = JSON().with_variant(JSONB, "postgresql")


class MaintenancePolicy(Base):
    """Singleton row holding the deployment-wide maintenance configuration.

    Only one row ever exists (enforced by the service layer). The ``thresholds``
    bundle is resolved from ``preset`` and may be overridden in the admin UI's
    Advanced section; the scanner reads it to drive scoring and recommendations.
    """

    __tablename__ = "maintenance_policy"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    scan_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # "off" | "hourly" | "daily" — coarse cadence, not a raw cron expression.
    scan_frequency: Mapped[str] = mapped_column(String(20), nullable=False, default="daily")
    # "conservative" | "balanced" | "aggressive".
    preset: Mapped[str] = mapped_column(String(20), nullable=False, default="balanced")
    thresholds: Mapped[dict] = mapped_column(_Json, nullable=False)
    max_tables_per_cycle: Mapped[int] = mapped_column(Integer, nullable=False, default=50)

    # Scanner state: cheap metadata tier vs. the slower orphan/glob tier, plus a
    # round-robin cursor ("schema.table") so coverage is fair across cycles.
    last_scan_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_deep_scan_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    scan_cursor: Mapped[str | None] = mapped_column(String(512), nullable=True)

    updated_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class TableHealthSample(Base):
    """One health snapshot for one table from one scan cycle.

    The latest sample per table is the table's current health; the full series
    powers storage-growth trends. Scoped by ``catalog_id`` (the table's true
    home); ``workspace_id`` is retained as a denormalized filter so the
    workspace-scoped health page reads without a join through the bindings.
    """

    __tablename__ = "table_health_sample"
    __table_args__ = (
        Index(
            "ix_table_health_sample_ident_time",
            "workspace_id",
            "schema_name",
            "table_name",
            "scanned_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    catalog_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("catalogs.id"), nullable=False)
    schema_name: Mapped[str] = mapped_column(String(255), nullable=False)
    table_name: Mapped[str] = mapped_column(String(255), nullable=False)
    scanned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    snapshot_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    data_file_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    manifest_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_data_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    avg_file_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    metadata_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    orphan_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    orphan_file_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    small_file_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)

    score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Per-dimension breakdown: {dimension: {score, value, detail}} for the UI.
    factors: Mapped[dict | None] = mapped_column(_Json, nullable=True)


class MaintenanceRecommendation(Base):
    """A justified maintenance suggestion for one table.

    At most one row per (table, kind): the scanner upserts and flips ``status``
    as conditions change, so the feed stays compact and honest. V1 surfaces
    these but never applies them — ``remediation`` holds external guidance and is
    the seam for a future one-click apply.
    """

    __tablename__ = "maintenance_recommendation"
    __table_args__ = (
        UniqueConstraint(
            "catalog_id",
            "schema_name",
            "table_name",
            "kind",
            name="uq_maintenance_recommendation_ident_kind",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    catalog_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("catalogs.id"), nullable=False)
    schema_name: Mapped[str] = mapped_column(String(255), nullable=False)
    table_name: Mapped[str] = mapped_column(String(255), nullable=False)

    # compact_small_files | expire_snapshots | rewrite_manifests | cleanup_orphans
    # | investigate_growth
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    # info | warning | critical
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    # low | medium | high
    confidence: Mapped[str] = mapped_column(String(20), nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    estimated_impact: Mapped[dict | None] = mapped_column(_Json, nullable=True)
    remediation: Mapped[dict | None] = mapped_column(_Json, nullable=True)
    # open | dismissed | resolved
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="open")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
