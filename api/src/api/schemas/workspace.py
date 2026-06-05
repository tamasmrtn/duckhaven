import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class WorkspaceCreate(BaseModel):
    slug: str
    name: str
    storage_backend_id: uuid.UUID | None = None


class WorkspaceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    slug: str
    name: str
    storage_backend_id: uuid.UUID
    storage_backend_kind: str
    created_at: datetime


class MemberOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    workspace_id: uuid.UUID
    user_id: uuid.UUID
    role: str


class AddMemberRequest(BaseModel):
    user_id: uuid.UUID
    role: str = "reader"
