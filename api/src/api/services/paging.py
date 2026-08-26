"""Keyset paging for the collections that grow without bound.

One helper so every paged endpoint cuts its pages the same way and returns the
same envelope. See docs/reference/api-conventions.md for which collections are
exempt from paging and why.

Keyset rather than offset: these collections are written to continuously, and an
offset page duplicates and skips rows as soon as anything is inserted ahead of
it. The cursor names the last row of the page by its full sort key, so the next
page starts strictly after it however much has landed in between.
"""

import base64
import binascii
import uuid
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import Select, and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute


def encode_cursor(row_id: uuid.UUID) -> str:
    """Opaque cursor naming the last row of a page, by id alone.

    Deliberately not the sort values. Round-tripping those through the cursor
    means comparing a bound Python value against a stored one, and the two can
    disagree on precision -- SQLite's ``CURRENT_TIMESTAMP`` has no microseconds
    while SQLAlchemy binds them, so an equality tie-break silently matches
    nothing and the page after the first comes back empty. Naming the row and
    letting the database compare stored-to-stored cannot drift.
    """
    return base64.urlsafe_b64encode(str(row_id).encode()).decode()


def decode_cursor(cursor: str) -> uuid.UUID:
    """Reverse :func:`encode_cursor`, or 422.

    A malformed cursor is a bad request, not a server fault, and ignoring it
    would quietly hand back the whole collection instead of a page.
    """
    try:
        return uuid.UUID(base64.urlsafe_b64decode(cursor.encode()).decode())
    except (ValueError, binascii.Error, UnicodeDecodeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"error": "invalid_cursor", "detail": "Malformed cursor."},
        ) from exc


def _after(columns: list[InstrumentedAttribute], anchor: uuid.UUID, descending: bool):
    """Rows strictly after the row ``anchor`` names, in the given ordering.

    Each sort value is read back with a scalar subquery so the comparison is
    stored-value against stored-value. Spelled as nested ORs rather than a
    row-value comparison: SQLite does not evaluate ``(a, b) > (x, y)`` against
    these column types, and the unit suite runs on SQLite while production runs
    on Postgres. These collections are small enough that the index shape does
    not pay for a dialect branch -- the query history keeps the row-value form
    where it matters.
    """
    id_column = columns[-1]
    values = [select(c).where(id_column == anchor).scalar_subquery() for c in columns]

    clauses = []
    for i, (column, value) in enumerate(zip(columns, values, strict=True)):
        ahead = column < value if descending else column > value
        ties = [columns[j] == values[j] for j in range(i)]
        clauses.append(and_(*ties, ahead) if ties else ahead)
    return or_(*clauses)


async def paginate(
    db: AsyncSession,
    stmt: Select,
    *,
    sort_columns: list[InstrumentedAttribute],
    limit: int,
    cursor: str | None,
    descending: bool = True,
) -> tuple[list[Any], str | None, bool]:
    """Run ``stmt`` as one keyset page.

    ``sort_columns`` is the full ordering and **must end in the row id**: it is
    both the tiebreak that makes the order total -- without it, rows sharing a
    value can be served twice or not at all -- and what the cursor names.
    ``stmt`` must carry its filters but neither ORDER BY nor LIMIT; both are
    applied here so the ordering and the cursor cannot disagree.

    Returns the rows, the cursor for the next page, and whether one exists.
    """
    if cursor is not None:
        stmt = stmt.where(_after(sort_columns, decode_cursor(cursor), descending))

    order = [c.desc() if descending else c.asc() for c in sort_columns]
    # One row more than asked for: its presence is `has_more`, and it costs a row
    # rather than the second aggregate a total would.
    rows = list((await db.execute(stmt.order_by(*order).limit(limit + 1))).all())

    has_more = len(rows) > limit
    rows = rows[:limit]
    next_cursor = None
    if has_more and rows:
        # `.all()` always yields Row, so the primary entity is at index 0 even
        # when the select carries joined columns alongside it.
        entity = rows[-1][0]
        next_cursor = encode_cursor(entity.id)
    return rows, next_cursor, has_more
