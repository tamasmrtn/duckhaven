import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class WorkspaceCreate(BaseModel):
    slug: str
    name: str


class DefaultAgentOut(BaseModel):
    """The agent a workspace runs on when a caller names none.

    Visible to every member, whether or not their own agent grants reach it: the
    workspace default is usable by all of them, so they need its name to see it.
    """

    id: uuid.UUID
    name: str
    provider: str | None = None
    lifecycle: str | None = None
    status: str


class WorkspaceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    slug: str
    name: str
    description: str | None = None
    created_at: datetime
    # Storage is catalog-scoped now; these summarize the workspace's *default*
    # catalog so existing UI (the switcher's backend badge) keeps rendering.
    default_catalog: str | None = None
    storage_backend_id: uuid.UUID | None = None
    storage_backend_kind: str | None = None
    # Whether repeated queries may be answered from the result cache.
    result_cache_enabled: bool = True
    # The compute that runs work nobody pointed at an agent. ``None`` = the server
    # picks a connected agent (or starts one when elastic compute is on).
    default_agent_id: uuid.UUID | None = None
    default_agent: DefaultAgentOut | None = None


class WorkspaceUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    # Owner only. Turning it off stops lookups at once; existing entries expire.
    result_cache_enabled: bool | None = None
    # Owner only. An explicit ``null`` clears it (back to server-picked compute); an
    # omitted field leaves it alone. Setting it needs ``use`` on an open agent and
    # ``operate`` on a restricted one.
    default_agent_id: uuid.UUID | None = None


class MemberOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    workspace_id: uuid.UUID
    user_id: uuid.UUID
    role: str


class AddMemberRequest(BaseModel):
    user_id: uuid.UUID
    role: str = "reader"


class AdminUserWorkspace(BaseModel):
    """A workspace and the target user's role in it (``None`` = not a member).

    Drives the admin "manage workspaces" view, which lists every workspace so an
    admin can grant or change a user's membership in one place.
    """

    workspace_id: uuid.UUID
    slug: str
    name: str
    role: str | None = None


class SetMembershipRequest(BaseModel):
    role: str
