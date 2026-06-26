from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from api.db.base import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    # Null for federated (OIDC/LDAP) users, who never set a local password.
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(50), nullable=False, default="user")
    theme: Mapped[str] = mapped_column(String(50), nullable=False, default="system")
    # How this account authenticates: "local" | "oidc" | "ldap". Guards against a
    # federated login binding to an existing local account (and vice versa).
    auth_provider: Mapped[str] = mapped_column(String(50), nullable=False, default="local")
    # The IdP-side identifier (OIDC `sub` / LDAP DN) for audit and re-matching.
    external_subject: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Cleared on offboarding to block new sessions and reject live ones at once.
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    credentials: Mapped[list[Credential]] = relationship(
        "Credential", back_populates="user", foreign_keys="Credential.user_id"
    )
    workspace_memberships: Mapped[list[WorkspaceMember]] = relationship(back_populates="user")


class Credential(Base):
    __tablename__ = "credentials"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True
    )
    agent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agents.id", ondelete="CASCADE"), nullable=True
    )
    kind: Mapped[str] = mapped_column(String(50), nullable=False)
    token: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    user: Mapped[User | None] = relationship(
        "User", back_populates="credentials", foreign_keys=[user_id]
    )
