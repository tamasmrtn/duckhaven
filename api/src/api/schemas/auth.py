import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class LoginRequest(BaseModel):
    email: str
    password: str


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    name: str
    role: str
    theme: str
    auth_provider: str
    is_active: bool
    # Populated for the authenticated user (`/me`) so the SPA can hide admin
    # navigation; left empty in bulk listings where it isn't needed.
    permissions: list[str] = []
    created_at: datetime


class CreateUserRequest(BaseModel):
    email: str
    name: str
    password: str
    role: str = "user"


class UpdateUserRequest(BaseModel):
    role: str | None = None
    is_active: bool | None = None


class AuthMethods(BaseModel):
    """Which login methods the SPA should surface."""

    local: bool
    ldap: bool
    oidc: bool
    oidc_label: str
