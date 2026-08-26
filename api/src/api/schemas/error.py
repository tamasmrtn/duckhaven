from typing import Any

from pydantic import BaseModel, Field


class ErrorOut(BaseModel):
    """The body of every 4xx and 5xx response.

    One shape for the whole API, so a client parses errors once. Branch on
    ``error``; show ``message``; read ``details`` only for the codes documented
    to carry it.
    """

    error: str = Field(
        description="Stable machine-readable code, snake_case. Branch on this, never on `message`.",
        examples=["sql_not_allowed"],
    )
    message: str = Field(
        description="Human-readable explanation, safe to display.",
        examples=["DDL is not permitted in this session."],
    )
    details: dict[str, Any] | None = Field(
        default=None,
        description="Endpoint-specific structured context. Absent unless documented.",
    )
