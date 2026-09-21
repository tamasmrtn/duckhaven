from __future__ import annotations

import uuid
from datetime import datetime
from typing import ClassVar

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from api.db.base import Base

# A kind pairs a table format with the metastore that arbitrates its commits, so
# it is one column, not two. Strings rather than an Enum, matching
# `storage_backends.kind` and keeping future kinds a data migration.
KIND_ICEBERG_POLARIS = "iceberg_polaris"
KIND_DUCKLAKE = "ducklake"
CATALOG_KINDS = frozenset({KIND_ICEBERG_POLARIS, KIND_DUCKLAKE})


class Catalog(Base):
    """A decoupled data domain: one catalog kind + one storage backend,
    attachable to many workspaces (M:N via :class:`WorkspaceCatalog`).

    ``slug`` is the identifier-safe DuckDB ATTACH alias and
    ``catalog.schema.table`` prefix.

    ``kind`` says where catalog metadata lives and which of ``polaris_name`` /
    ``metadata_schema`` is set (enforced by ``ck_catalogs_kind_identity``).
    Storage is orthogonal: both kinds bind to a ``StorageBackend`` the same way
    (I4). Both identity columns are stored rather than derived, so a renamed
    catalog keeps pointing at its physical metastore.
    """

    __tablename__ = "catalogs"
    __table_args__ = (
        CheckConstraint(
            "(kind = 'iceberg_polaris' AND polaris_name IS NOT NULL "
            "AND metadata_schema IS NULL) OR "
            "(kind = 'ducklake' AND metadata_schema IS NOT NULL "
            "AND polaris_name IS NULL)",
            name="ck_catalogs_kind_identity",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    slug: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    kind: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=KIND_ICEBERG_POLARIS
    )
    polaris_name: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True)
    # 63 is Postgres's identifier limit; a longer name would be truncated there.
    metadata_schema: Mapped[str | None] = mapped_column(String(63), unique=True, nullable=True)
    storage_backend_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("storage_backends.id"), nullable=False
    )
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    storage_backend: Mapped[StorageBackend] = relationship(back_populates="catalogs")
    # The PostgreSQL login this catalog's agents authenticate with (DuckLake
    # only; None for Iceberg). View-only and eager-loaded by the catalog
    # resolvers, so the dispatch path can mint an attach payload without a
    # second round trip -- it builds one of these per catalog per query.
    # Set only between minting a DuckLake catalog's password and writing its
    # credential row, which cannot happen until the flush gives this an id.
    # A plain attribute, not a column: it is never persisted and never read
    # again once the row exists.
    pending_ducklake_password: ClassVar[str | None] = None

    ducklake_credential: Mapped[Credential | None] = relationship(
        "Credential",
        primaryjoin=(
            "and_(Catalog.id == foreign(Credential.catalog_id), Credential.kind == 'ducklake_role')"
        ),
        viewonly=True,
        uselist=False,
    )
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
    # "open" (default) = the workspace role governs every schema/table, today's
    # behavior. "scoped" = access is narrowed by CatalogGrant rows for this
    # catalog (opt-in per attachment, so untouched catalogs are unaffected).
    access_mode: Mapped[str] = mapped_column(String(20), nullable=False, server_default="open")
    attached_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    attached_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)

    workspace: Mapped[Workspace] = relationship(back_populates="catalog_links")
    catalog: Mapped[Catalog] = relationship(back_populates="workspace_links")
