from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from api.db.base import Base

# Service accounts are User rows with this provider: no local password, so they
# can never complete a password/OIDC/LDAP login — only present a PAT. Defined here
# beside the column it describes, so the routers and services that branch on it
# cannot drift apart.
SERVICE_ACCOUNT_PROVIDER = "service_account"


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    # Null for federated (OIDC/LDAP) users, who never set a local password.
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(50), nullable=False, default="user")
    theme: Mapped[str] = mapped_column(String(50), nullable=False, default="system")
    # How this account authenticates: "local", "ldap", or "oidc:<provider id>" (a
    # bare "oidc" on rows from before providers were told apart). Guards against a
    # federated login binding to an account of another provider.
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
    # Set for kind="ducklake_role": that catalog's own PostgreSQL login password.
    catalog_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("catalogs.id", ondelete="CASCADE"), unique=True, nullable=True
    )
    kind: Mapped[str] = mapped_column(String(50), nullable=False)
    # Raw value for sessions, agent tokens and DuckLake role passwords; NULL for
    # PATs, which store only a hash. Not encrypted at rest, but DuckLake roles
    # cannot connect to this database.
    token: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True)
    # SHA-256 hex digest of a PAT secret (kind="pat"); NULL for other kinds. The
    # unique index gives an O(1) lookup by the hash of a presented bearer token.
    token_hash: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Set on the assistant's per-turn PAT: the person the turn is for. A request
    # with it may do only what both that person and ``user_id`` may do (see
    # services/delegation.py). The person's deletion takes the token with them.
    on_behalf_of_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    user: Mapped[User | None] = relationship(
        "User", back_populates="credentials", foreign_keys=[user_id]
    )
    on_behalf_of: Mapped[User | None] = relationship("User", foreign_keys=[on_behalf_of_id])
