from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from api.db.base import Base


class CatalogGrant(Base):
    """A scoped access grant for a principal on a catalog, schema, or table.

    Only consulted when the catalog's attachment is in ``access_mode="scoped"``
    (:class:`WorkspaceCatalog`); ``"open"`` catalogs fall back to the plain
    workspace role. The principal is a ``users.id`` — a human or a service
    account alike (both carry ``WorkspaceMember`` rows), so no principal-type
    discriminator is needed.

    Granularity is expressed by which name columns are set, forming the
    ``table -> schema -> catalog`` hierarchy walked at check time:

    - both ``NULL`` -> catalog-level (covers every schema/table, incl. future)
    - ``schema_name`` set, ``table_name`` ``NULL`` -> schema-level (covers every
      current *and future* table in that schema)
    - both set -> table-level

    ``tier`` extends the role vocabulary with a discovery-only level:
    ``metadata < reader < writer``. ``owner`` is deliberately not grantable
    below the catalog (it is a workspace role only). The grant is an ACL row
    keyed by *name* (like ``table_metadata``), not a cache of catalog structure
    (I3).
    """

    __tablename__ = "catalog_grants"
    __table_args__ = (
        # One grant per principal per node. NULL name columns can't use a plain
        # UNIQUE (Postgres treats NULLs as distinct), so key on COALESCE — works
        # on Postgres and the SQLite test backend alike.
        Index(
            "uq_catalog_grants_node",
            "user_id",
            "catalog_id",
            text("coalesce(schema_name, '')"),
            text("coalesce(table_name, '')"),
            unique=True,
        ),
        CheckConstraint(
            "table_name IS NULL OR schema_name IS NOT NULL",
            name="ck_catalog_grants_table_needs_schema",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    catalog_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("catalogs.id", ondelete="CASCADE"), nullable=False
    )
    schema_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    table_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    tier: Mapped[str] = mapped_column(String(20), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
