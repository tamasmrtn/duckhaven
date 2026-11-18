"""How an agent attaches the ``duckhaven`` system catalog: read-only, as the store's
reader login, for a statement ``authorize`` has let read it.

It is sent last in a dispatch frame's catalogs, never at session open and never in
``workspace_catalogs``, so an agent cannot ask for it on demand either.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from api.config import settings
from api.models.agent import Agent
from api.models.user import Credential
from api.services.agent_capabilities import (
    agent_supports_catalog_kind,
    agent_supports_feature,
    required_catalog_extensions,
)
from api.services.runtimes import AgentNotDispatchable
from api.services.system_catalog import store
from duckhaven_shared.system_catalog import (
    KIND_SYSTEM,
    SYSTEM_CATALOG_ALIAS,
    SYSTEM_CATALOG_FEATURE,
)

UNAVAILABLE = "system_catalog_unavailable"


def agent_can_attach(agent: Agent | None) -> bool:
    capabilities = agent.capabilities if agent is not None else None
    return agent_supports_feature(
        capabilities, SYSTEM_CATALOG_FEATURE
    ) and agent_supports_catalog_kind(capabilities, KIND_SYSTEM)


def is_available() -> bool:
    """Whether this replica can send the system catalog: turned on, and its store
    created since it was last found broken."""
    return settings.system_catalog_enabled and store.is_available()


async def descriptor(db: AsyncSession, agent: Agent) -> dict[str, object]:
    """The catalog descriptor that has ``agent`` attach the system catalog.

    Raises ``AgentNotDispatchable`` when the deployment cannot serve it or the
    agent cannot attach it.
    """
    password = None
    if is_available():
        password = await db.scalar(
            sa.select(Credential.token).where(Credential.kind == store.READER_CREDENTIAL_KIND)
        )
    if not password:
        raise AgentNotDispatchable(
            UNAVAILABLE,
            "The duckhaven system catalog is not available on this deployment: it is "
            "turned off, or its database cannot be reached.",
        )
    if not agent_supports_feature(agent.capabilities, SYSTEM_CATALOG_FEATURE):
        raise AgentNotDispatchable(
            "agent_incompatible",
            f"Agent '{agent.name}' is too old to read the duckhaven system catalog. "
            "Update the agent.",
        )
    if not agent_supports_catalog_kind(agent.capabilities, KIND_SYSTEM):
        missing = ", ".join(required_catalog_extensions(KIND_SYSTEM))
        raise AgentNotDispatchable(
            "agent_incompatible",
            f"Agent '{agent.name}' is missing the '{missing}' extension required "
            "by the duckhaven system catalog.",
        )
    return {
        "slug": SYSTEM_CATALOG_ALIAS,
        "kind": KIND_SYSTEM,
        "backend": {},
        "default_schema": "access",
        "meta": {
            "host": settings.system_catalog_agent_host,
            "port": settings.system_catalog_agent_port,
            "database": settings.system_catalog_agent_database,
            "user": store.READER_ROLE,
            "password": password,
        },
    }
