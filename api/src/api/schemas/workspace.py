import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class WorkspaceCreate(BaseModel):
    slug: str
    name: str


class WorkspaceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    slug: str
    name: str
    created_at: datetime
    # Storage is catalog-scoped now; these summarize the workspace's *default*
    # catalog so existing UI (the switcher's backend badge) keeps rendering.
    default_catalog: str | None = None
    storage_backend_id: uuid.UUID | None = None
    storage_backend_kind: str | None = None


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
