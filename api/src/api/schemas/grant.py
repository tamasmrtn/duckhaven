import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator

Tier = Literal["metadata", "reader", "writer"]
AccessMode = Literal["open", "scoped"]


class AccessModeUpdate(BaseModel):
    access_mode: AccessMode


class GrantUpsert(BaseModel):
    user_id: uuid.UUID
    schema_name: str | None = None
    table_name: str | None = None
    tier: Tier

    @model_validator(mode="after")
    def _table_needs_schema(self) -> GrantUpsert:
        if self.table_name is not None and self.schema_name is None:
            raise ValueError("table_name requires schema_name")
        return self


class GrantOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    user_name: str | None = None
    schema_name: str | None
    table_name: str | None
    tier: str
    created_at: datetime


class GrantPrincipalOut(BaseModel):
    """A candidate grant principal — a workspace member, human or service account."""

    user_id: uuid.UUID
    name: str
    email: str
    role: str
    is_service_account: bool


class CatalogGrantsOut(BaseModel):
    access_mode: str
    grants: list[GrantOut]
    principals: list[GrantPrincipalOut]
