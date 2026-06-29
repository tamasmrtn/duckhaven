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


class OidcProviderInfo(BaseModel):
    """A configured SSO provider the login page renders a button for."""

    id: str
    label: str


class AuthMethods(BaseModel):
    """Which login methods the SPA should surface."""

    local: bool
    ldap: bool
    # One entry per configured OIDC provider; empty when SSO is off.
    oidc_providers: list[OidcProviderInfo] = []
