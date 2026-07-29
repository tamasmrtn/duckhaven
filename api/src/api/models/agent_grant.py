from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from api.db.base import Base


class AgentGrant(Base):
    """One principal's access tier on one agent.

    The per-agent overlay on the global RBAC model: ``agents:manage`` still confers
    full access to every agent, and this table adds *specific* access to *specific*
    agents for principals who hold no global agent permission.

    The principal is either a user or a workspace, never both (``CHECK``). Both are
    needed and they are not substitutes: a workspace grant is how "everyone in
    Analytics may run work on the shared ADLS agent" stays manageable as the team
    changes, while a user grant is how "Dana may restart it" names a person. A
    principal's effective tier is the *maximum* over their direct grant and the
    grants on every workspace they belong to — grants are additive, and there is no
    deny.

    A user grant covers service accounts too: they are ``User`` rows like anyone
    else, so no principal-type discriminator is needed on that side.

    ``tier`` orders ``use < operate < admin`` (see
    :mod:`api.services.agent_access`). Workspace grants are capped at ``operate``
    by the API — ``admin`` includes granting, and delegating that to "whoever is
    currently a member of workspace W" would make the ACL unauditable.

    ``created_by`` is ``SET NULL`` so deleting the granter never deletes the grant.
    """

    __tablename__ = "agent_grants"
    __table_args__ = (
        CheckConstraint(
            "(user_id IS NULL) <> (workspace_id IS NULL)",
            name="ck_agent_grants_one_principal",
        ),
        # One grant per principal per agent. Partial unique indexes rather than
        # catalog_grants' COALESCE trick: these are UUID columns, and coalescing one
        # to '' needs a Postgres-only ::text cast that SQLite rejects. Partial
        # indexes behave identically on both backends.
        Index(
            "uq_agent_grants_user",
            "agent_id",
            "user_id",
            unique=True,
            postgresql_where=text("user_id IS NOT NULL"),
            sqlite_where=text("user_id IS NOT NULL"),
        ),
        Index(
            "uq_agent_grants_workspace",
            "agent_id",
            "workspace_id",
            unique=True,
            postgresql_where=text("workspace_id IS NOT NULL"),
            sqlite_where=text("workspace_id IS NOT NULL"),
        ),
        # The per-request lookup is "every grant reaching this caller", keyed on the
        # principal columns rather than the agent.
        Index("ix_agent_grants_user_id", "user_id"),
        Index("ix_agent_grants_workspace_id", "workspace_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agents.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True
    )
    workspace_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=True
    )
    tier: Mapped[str] = mapped_column(String(20), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
