from pydantic import BaseModel, Field


class SetupStatus(BaseModel):
    needs_admin: bool


class FirstAdminRequest(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=8, max_length=255)
    name: str = Field(min_length=1, max_length=255, default="Admin")
