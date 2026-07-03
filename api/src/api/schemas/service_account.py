import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ServiceAccountOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    email: str
    role: str
    is_active: bool
    created_at: datetime
    # Number of live (issued, not yet revoked) PATs bound to this account.
    pat_count: int = 0


class CreateServiceAccountRequest(BaseModel):
    name: str
    # Global role. Defaults to "user" — zero global permissions — so a new
    # service account is never accidentally an admin; escalate deliberately.
    role: str = "user"


class UpdateServiceAccountRequest(BaseModel):
    role: str | None = None
    is_active: bool | None = None


class PatCreateRequest(BaseModel):
    # Days until the PAT expires; None means it never expires. Defaults to 90.
    expires_in_days: int | None = 90


class PatTokenOut(BaseModel):
    """One-time issuance response: the raw secret is returned here exactly once
    and never stored or shown again."""

    id: uuid.UUID
    token: str
    expires_at: datetime | None


class PatOut(BaseModel):
    """PAT metadata for the management list — never the secret or its hash."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    created_at: datetime
    expires_at: datetime | None
