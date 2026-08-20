import uuid
from typing import Literal

from pydantic import BaseModel


class SearchResultOut(BaseModel):
    """A single command-palette (⌘K) result.

    Deliberately minimal — just enough for the palette to show disambiguating
    parent-path context and build its destination route without a second
    round-trip. Table/schema results are name-addressed (catalog/schema/table
    have no separate routable id); saved queries carry their real id plus
    enough to seed a worksheet tab the same way SavedQueriesPage does.
    """

    type: Literal["schema", "table", "saved_query"]
    name: str
    catalog: str | None = None
    schema_name: str | None = None
    id: uuid.UUID | None = None
    sql: str | None = None
    default_agent_id: uuid.UUID | None = None
