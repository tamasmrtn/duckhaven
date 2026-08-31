import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


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


class SelfPatCreateRequest(BaseModel):
    """Self-service issuance, which is always bounded.

    The service-account form above accepts ``None`` (never expires) because an
    admin issues it deliberately for an unattended pipeline that nobody will be
    around to re-authenticate. A token a user mints for themselves is not that,
    so an expiry is mandatory here and capped at a year -- long enough that
    rotation is not a nuisance, short enough that a forgotten laptop is not a
    permanent credential.
    """

    expires_in_days: int = Field(default=90, ge=1, le=365)


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


class SelfPatOut(PatOut):
    """The same metadata, plus which token is making this very request.

    Only the hash of a token is stored, so a listing can never identify one by
    value the way a name or a visible prefix would. Marking the caller's own
    credential is what lets a client say "the token you are using expires on
    Friday" instead of listing three indistinguishable rows and leaving the
    reader to guess. GitLab solves the same problem with a separate
    `/personal_access_tokens/self` route; a flag answers it in one request.
    """

    current: bool = False
