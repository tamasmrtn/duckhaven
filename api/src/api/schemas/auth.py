import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

# How the main navigation is laid out. Null on the user means "never chose".
SidebarMode = Literal["expanded", "collapsed", "hover"]
# The app's colors; "system" follows the operating system's light or dark mode.
ThemeMode = Literal["light", "dark", "system"]


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
    sidebar: SidebarMode | None = None
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


class UpdateMeRequest(BaseModel):
    """The caller's own preferences. Only the fields sent are changed."""

    model_config = ConfigDict(extra="forbid")

    theme: ThemeMode | None = None
    sidebar: SidebarMode | None = None


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
