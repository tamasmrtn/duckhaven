from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from api.db.base import Base

# The catalog kinds DuckHaven can provision. A *kind* is the pairing of a table
# format with the metastore that arbitrates its commits — the two are not
# independently selectable, which is why this is one column and not two.
#
# Deliberately not an Enum: this schema has no enum types anywhere (see
# `storage_backends.kind`), and widening a String is a data migration rather than
# a type migration.
KIND_ICEBERG_POLARIS = "iceberg_polaris"
KIND_DUCKLAKE = "ducklake"
CATALOG_KINDS = frozenset({KIND_ICEBERG_POLARIS, KIND_DUCKLAKE})


class Catalog(Base):
    """A decoupled data domain: one catalog kind + one storage backend,
    attachable to many workspaces (M:N via :class:`WorkspaceCatalog`).

    ``slug`` is an identifier-safe handle (``^[a-z][a-z0-9_]*$``) used as the
    DuckDB ATTACH alias and in ``catalog.schema.table`` addressing.

    ``kind`` says where catalog *metadata* lives, and decides which half of the
    identity pair below is populated. Storage is an orthogonal axis: both kinds
    bind to a ``StorageBackend`` the same way, and invariant I4 (one catalog, one
    backend) holds for both.

    - ``iceberg_polaris`` — Apache Iceberg tables in an Apache Polaris catalog.
      ``polaris_name`` is the Polaris warehouse/catalog name (globally unique);
      it is stored explicitly rather than derived so migrated catalogs keep their
      legacy name (the originating workspace slug) without a Polaris rename.
    - ``ducklake`` — DuckLake tables whose catalog is a set of ``ducklake_*``
      tables in one schema of the ``ducklake`` database. ``metadata_schema`` is
      that Postgres schema name, stored explicitly for the same reason
      ``polaris_name`` is: a catalog adopted from an existing DuckLake, or one
      whose slug changed, must keep pointing at its physical schema.

    Exactly one of ``polaris_name`` / ``metadata_schema`` is set, enforced by
    ``ck_catalogs_kind_identity`` rather than left to the service layer — a row
    with neither is a catalog nobody can open.
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
    # 63 is Postgres's identifier limit, so a longer name would be silently
    # truncated by the server and stop matching this row.
    metadata_schema: Mapped[str | None] = mapped_column(String(63), unique=True, nullable=True)
    storage_backend_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("storage_backends.id"), nullable=False
    )
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
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
