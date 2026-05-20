import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class StorageBackendCreate(BaseModel):
    kind: str
    name: str
    root_uri: str
    uc_storage_credential_id: str | None = None


class StorageBackendOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    kind: str
    name: str
    root_uri: str
    uc_storage_credential_id: str | None
    created_by: uuid.UUID
    created_at: datetime
    workspace_count: int = 0
    uc_credential_valid: bool | None = None
