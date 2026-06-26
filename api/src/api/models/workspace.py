from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from api.db.base import Base


class Workspace(Base):
    __tablename__ = "workspaces"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    slug: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    # Storage now belongs to catalogs (M:N, I4 catalog-scoped); a workspace's
    # storage is the union of its attached catalogs' backends.
    catalog_links: Mapped[list[WorkspaceCatalog]] = relationship(
        back_populates="workspace", cascade="all, delete-orphan"
    )
    members: Mapped[list[WorkspaceMember]] = relationship(back_populates="workspace")
    queries: Mapped[list[Query]] = relationship(back_populates="workspace")
    saved_queries: Mapped[list[SavedQuery]] = relationship(back_populates="workspace")


class WorkspaceMember(Base):
    __tablename__ = "workspace_members"

    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), primary_key=True)
    role: Mapped[str] = mapped_column(String(50), nullable=False, default="reader")

    workspace: Mapped[Workspace] = relationship(back_populates="members")
    user: Mapped[User] = relationship(back_populates="workspace_memberships")
