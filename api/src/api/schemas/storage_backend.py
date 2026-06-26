import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, model_validator


class S3Config(BaseModel):
    """Credential config for an external AWS S3 backend.

    All fields are identifiers, not secrets: Polaris assumes ``role_arn`` via STS
    (optionally guarded by ``external_id``) to vend short-lived scoped creds. No
    static access key is ever stored.
    """

    model_config = ConfigDict(extra="forbid")

    role_arn: str
    region: str
    external_id: str | None = None
    # Optional S3-compatible endpoint override (kept for parity with the bundled
    # object_store path); omit for real AWS S3.
    endpoint: str | None = None
    path_style_access: bool | None = None


class AdlsConfig(BaseModel):
    """Credential config for an external Azure ADLS Gen2 backend.

    Polaris vends a scoped SAS token through the Entra app identified by
    ``tenant_id`` after the operator grants consent. No account key is stored.
    """

    model_config = ConfigDict(extra="forbid")

    tenant_id: str
    multi_tenant_app_name: str | None = None
    consent_url: str | None = None
    # Set true for ADLS Gen2 hierarchical-namespace accounts so Polaris can
    # down-scope SAS tokens to specific paths.
    hierarchical: bool | None = None


class StorageBackendCreate(BaseModel):
    kind: str
    name: str
    root_uri: str
    config: dict | None = None

    @model_validator(mode="after")
    def _validate_config_for_kind(self) -> StorageBackendCreate:
        """External backends require a kind-matched config; object_store rejects one."""
        if self.kind == "s3":
            S3Config.model_validate(self.config or {})
        elif self.kind == "adls_gen2":
            AdlsConfig.model_validate(self.config or {})
        elif self.config:
            raise ValueError("object_store backends do not take a config")
        return self


class StorageBackendOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    kind: str
    name: str
    root_uri: str
    config: dict | None = None
    created_by: uuid.UUID
    created_at: datetime
    workspace_count: int = 0


class StorageBackendHealth(BaseModel):
    """Result of validating that a backend's vended credentials can reach storage."""

    valid: bool
    detail: str
