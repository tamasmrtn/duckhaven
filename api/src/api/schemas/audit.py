"""The audit log as the REST API returns it: one event, grouped by who, where and what."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

Category = Literal["identity", "access", "credential", "auth", "authz"]
Outcome = Literal["success", "denied", "failure"]


class AuditActor(BaseModel):
    """Who acted. For a delegated call, the service account and the person it acted for."""

    id: uuid.UUID | None
    email: str | None
    type: Literal["user", "service_account", "system", "idp", "anonymous"]
    on_behalf_of_id: uuid.UUID | None
    on_behalf_of_email: str | None
    auth_method: Literal["session", "pat", "system"] | None
    #: The session or token the request presented; never its secret.
    credential_id: uuid.UUID | None


class AuditRequest(BaseModel):
    id: str | None = Field(description="Also sent back as the X-Request-ID response header.")
    source_ip: str | None
    user_agent: str | None


class AuditWorkspace(BaseModel):
    id: uuid.UUID | None
    slug: str | None = Field(description="As it was at the time; kept after a delete.")


class AuditTarget(BaseModel):
    type: str
    id: str | None
    name: str | None = Field(description="As it was at the time, such as sales.analytics.orders.")


class AuditSubject(BaseModel):
    """Whose access changed, when that is a person or service account."""

    id: uuid.UUID | None
    email: str | None


class AuditEventOut(BaseModel):
    id: uuid.UUID
    event_time: datetime
    event_version: int
    category: Category
    action: str = Field(description="See docs/reference/audit-events.md.")
    label: str = Field(description="A short description of the action, for display.")
    outcome: Outcome
    actor: AuditActor
    request: AuditRequest
    workspace: AuditWorkspace
    target: AuditTarget
    subject: AuditSubject
    self_change: bool = Field(
        description="The subject is the actor, or the person a delegated call acted for."
    )
    before: dict[str, Any] | None
    after: dict[str, Any] | None
    details: dict[str, Any] | None
    error_code: str | None
