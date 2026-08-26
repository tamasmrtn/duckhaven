from pydantic import BaseModel, Field


class Page[T](BaseModel):
    """One page of a collection that grows without bound.

    The same three fields for every such endpoint, so a client pages once and
    reuses it everywhere. See docs/reference/api-conventions.md for which
    collections are exempt and why.

    Deliberately carries no total: counting the rows behind a page means a second
    aggregate over the same predicates on every request, to render a number that
    is stale the moment anything is written. ``has_more`` costs nothing -- the
    endpoint asks for one row more than it returns -- and says what the caller
    actually needs to know.
    """

    items: list[T]
    cursor: str | None = Field(
        default=None,
        description="Opaque; feed back as `cursor` for the next page. Null on the last page.",
    )
    has_more: bool = Field(
        default=False,
        description="Whether another page follows.",
    )
