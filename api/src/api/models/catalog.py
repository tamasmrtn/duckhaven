from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from api.db.base import Base


class Catalog(Base):
    """A decoupled data domain: its own Polaris catalog + storage backend,
    attachable to many workspaces (M:N via :class:`WorkspaceCatalog`).

    ``slug`` is an identifier-safe handle (``^[a-z][a-z0-9_]*$``) used as the
    DuckDB ATTACH alias and in ``catalog.schema.table`` addressing. ``polaris_name``
    is the Polaris warehouse/catalog name (globally unique); it is stored
    explicitly rather than derived so migrated catalogs keep their legacy name
    (the originating workspace slug) without a Polaris rename.
    """

    __tablename__ = "catalogs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    slug: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    polaris_name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    storage_backend_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("storage_backends.id"), nullable=False
    )
    # The built-in system catalog is DuckHaven-owned and read-only: attached to
    # every workspace by default, never detachable/droppable, and (I8) never
    # writable through the query path. It has no human creator, so ``created_by``
    # is nullable.
    is_system: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    storage_backend: Mapped[StorageBackend] = relationship(back_populates="catalogs")
    workspace_links: Mapped[list[WorkspaceCatalog]] = relationship(
        back_populates="catalog", cascade="all, delete-orphan"
    )


class WorkspaceCatalog(Base):
    """M:N binding of a catalog to a workspace.

    Exactly one binding per workspace has ``is_default=True`` — the catalog
    ``USE``d for unqualified table names so existing single-catalog SQL keeps
    resolving. The service layer enforces the single-default invariant.
    """

    __tablename__ = "workspace_catalogs"

    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), primary_key=True)
    catalog_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("catalogs.id"), primary_key=True)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    attached_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    attached_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)

    workspace: Mapped[Workspace] = relationship(back_populates="catalog_links")
    catalog: Mapped[Catalog] = relationship(back_populates="workspace_links")
