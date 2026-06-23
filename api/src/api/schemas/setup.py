from pydantic import BaseModel, Field


class SetupStatus(BaseModel):
    needs_admin: bool


class SystemStorageChoice(BaseModel):
    """The storage backend the admin picks for the system catalog during setup.

    Defaults to the bundled object store (MinIO) at the warehouse-bucket root, so
    a vanilla install needs no input. ``s3``/``adls_gen2`` are operator-owned
    external object stores and require a ``root_uri``.
    """

    kind: str = "object_store"
    name: str = "System"
    root_uri: str = ""
    uc_storage_credential_id: str | None = None


class FirstAdminRequest(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=8, max_length=255)
    name: str = Field(min_length=1, max_length=255, default="Admin")
    # Storage backend for the built-in system catalog. The setup wizard prompts
    # for this; omitting it falls back to the bundled object store.
    system_storage: SystemStorageChoice = Field(default_factory=SystemStorageChoice)
