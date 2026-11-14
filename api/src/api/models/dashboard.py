"""Dashboards: a workspace's stored dashboard files, their history and what they read.

A dashboard holds up to two versions of its file. ``spec`` is the published
version readers see, and ``draft_spec`` the working copy writers edit. Saving
writes the draft, and publishing moves it into ``spec`` and records a revision.
``title``, ``description`` and ``labels`` copy the published file, or the draft
until the first publish, so a listing needs no JSON.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from api.db.base import Base

# JSONB on Postgres, plain JSON on other dialects (SQLite under unit tests).
_Json = JSON().with_variant(JSONB, "postgresql")

STATUSES = ("draft", "published")
SOURCES = ("ui", "git")
#: How the last change arrived. The assistant and MCP set theirs when they can write.
VIA = ("ui", "rest", "cli", "assistant", "mcp")
REF_STATES = ("draft", "published")
REF_KINDS = ("model", "metric", "dimension", "table")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class Dashboard(Base):
    __tablename__ = "dashboards"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    labels: Mapped[list] = mapped_column(_Json, nullable=False, default=list)
    #: ``draft`` until the first publish, then ``published``.
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft")

    # The published file, null until the first publish. ``source_text`` is the
    # YAML it was written as, kept so comments and layout survive; null when it
    # was sent as JSON.
    spec: Mapped[dict | None] = mapped_column(_Json, nullable=True)
    source_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    spec_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # The working copy, null when there are no unpublished changes.
    draft_spec: Mapped[dict | None] = mapped_column(_Json, nullable=True)
    draft_source_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: The format version of the file the columns above copy.
    spec_version: Mapped[int] = mapped_column(Integer, nullable=False)

    # Where the file lives. A ``git`` dashboard is changed only by applying its file.
    source: Mapped[str] = mapped_column(String(8), nullable=False, default="ui")
    source_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)

    #: Bumped by every change, so two editors cannot overwrite each other.
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    updated_via: Mapped[str] = mapped_column(String(16), nullable=False, default="rest")
    #: The client that made the last change, as ``name/version`` from its User-Agent.
    client: Mapped[str | None] = mapped_column(String(255), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    published_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    @property
    def has_draft(self) -> bool:
        return self.draft_spec is not None

    @property
    def managed(self) -> bool:
        return self.source == "git"

    __table_args__ = (
        Index("uq_dashboards_ws_lower_name", "workspace_id", func.lower(name), unique=True),
        CheckConstraint(_in("status", STATUSES), name="ck_dashboards_status"),
        CheckConstraint(_in("source", SOURCES), name="ck_dashboards_source"),
        CheckConstraint(_in("updated_via", VIA), name="ck_dashboards_updated_via"),
    )


class DashboardRevision(Base):
    """One publish, kept as it was. Numbered from 1 per dashboard."""

    __tablename__ = "dashboard_revisions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    dashboard_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("dashboards.id", ondelete="CASCADE"), nullable=False
    )
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    spec: Mapped[dict] = mapped_column(_Json, nullable=False)
    source_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    spec_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    published_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    published_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index("uq_dashboard_revisions_number", "dashboard_id", "number", unique=True),
    )


class DashboardRef(Base):
    """One thing a dashboard reads, so a change to it can find the dashboards it breaks.

    ``model`` and ``metric`` or ``dimension`` refs name a semantic model and, for
    the latter two, the model they belong to. A ``table`` ref names a table as an
    SQL widget wrote it. Rebuilt whenever the draft or the published file changes.
    """

    __tablename__ = "dashboard_refs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    dashboard_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("dashboards.id", ondelete="CASCADE"), nullable=False
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    model: Mapped[str | None] = mapped_column(String(255), nullable=True)
    name: Mapped[str] = mapped_column(String(767), nullable=False)

    __table_args__ = (
        Index("ix_dashboard_refs_lookup", "workspace_id", "kind", "model", "name"),
        Index("ix_dashboard_refs_dashboard", "dashboard_id", "state"),
        CheckConstraint(_in("state", REF_STATES), name="ck_dashboard_refs_state"),
        CheckConstraint(_in("kind", REF_KINDS), name="ck_dashboard_refs_kind"),
    )


class DashboardAlias(Base):
    """A name a dashboard had before a rename, so old links still find it.

    A live dashboard's name always wins: creating or renaming a dashboard to an
    alias's name removes the alias.
    """

    __tablename__ = "dashboard_aliases"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    dashboard_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("dashboards.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index("uq_dashboard_aliases_ws_lower_name", "workspace_id", func.lower(name), unique=True),
        Index("ix_dashboard_aliases_dashboard", "dashboard_id"),
    )


class DashboardPreview(Base):
    """A dashboard file one writer is trying out, before it is saved anywhere.

    An editor or ``dh dashboard preview`` replaces the file as it changes, and
    the page showing the preview runs it as that writer. Only its creator can
    read or run it. It expires two hours after it last changed.
    """

    __tablename__ = "dashboard_previews"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    created_by: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    spec: Mapped[dict] = mapped_column(_Json, nullable=False)
    #: The YAML it was sent as; null when it was sent as JSON.
    source_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    spec_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (Index("ix_dashboard_previews_expires_at", "expires_at"),)
