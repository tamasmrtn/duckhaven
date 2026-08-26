"""Keyset paging for the collections that grow without bound.

One helper so every paged endpoint cuts its pages the same way and returns the
same envelope. See docs/reference/api-conventions.md for which collections are
exempt from paging and why.

Keyset rather than offset: these collections are written to continuously, and an
offset page duplicates and skips rows as soon as anything is inserted ahead of
it. The cursor names the last row of the page, and the predicate reads that
row's sort values back out of the table, so the comparison is stored-value
against stored-value.

That indirection is the point. Carrying the values in the cursor instead means
comparing a value bound from the client against one stored in the table, and the
two can disagree on precision -- SQLite writes ``CURRENT_TIMESTAMP`` without
microseconds while SQLAlchemy binds them, which silently matches nothing.
"""

import base64
import binascii
import uuid
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import Select, and_, or_, select, tuple_
from sqlalchemy.sql import operators
from sqlalchemy.sql.elements import UnaryExpression


def encode_cursor(row_id: uuid.UUID) -> str:
    """Opaque cursor naming the last row of a page, by id."""
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


def _split(sort: list[UnaryExpression]) -> tuple[list[Any], list[bool]]:
    """Read the columns and their directions off an ``order_by`` list."""
    columns, descending = [], []
    for expression in sort:
        columns.append(expression.element)
        descending.append(expression.modifier is operators.desc_op)
    return columns, descending


def _after(columns: list[Any], descending: list[bool], anchor: uuid.UUID):
    """Rows strictly after the row ``anchor`` names, in the given ordering.

    Each sort value is read back with a scalar subquery, so the comparison never
    round-trips a value through Python.

    When every column sorts the same way this is a row-value comparison, which
    Postgres can turn into a single index range scan. A mixed ordering -- most
    severe first, then newest -- has no row-value spelling, so it expands to the
    equivalent nested ORs instead.
    """
    id_column = columns[-1]
    values = [select(c).where(id_column == anchor).scalar_subquery() for c in columns]

    if all(descending) or not any(descending):
        row, anchor_row = tuple_(*columns), tuple_(*values)
        return row < anchor_row if descending[0] else row > anchor_row

    clauses = []
    for i, (column, value, desc) in enumerate(zip(columns, values, descending, strict=True)):
        ahead = column < value if desc else column > value
        ties = [columns[j] == values[j] for j in range(i)]
        clauses.append(and_(*ties, ahead) if ties else ahead)
    return or_(*clauses)


async def paginate(
    db,
    stmt: Select,
    *,
    sort: list[UnaryExpression],
    limit: int,
    cursor: str | None,
) -> tuple[list[Any], str | None, bool]:
    """Run ``stmt`` as one keyset page.

    ``sort`` is the full ordering as ``column.asc()``/``column.desc()`` terms and
    **must end in the row id**: it is both the tiebreak that makes the order
    total -- without it, rows sharing a value can be served twice or not at all
    -- and what the cursor names. ``stmt`` carries the filters but neither ORDER
    BY nor LIMIT; both are applied here so the ordering and the cursor cannot
    disagree.

    Returns the rows, the cursor for the next page, and whether one exists.
    """
    columns, descending = _split(sort)
    id_column = columns[-1]

    if cursor is not None:
        anchor = decode_cursor(cursor)
        # The anchor carries the page's position, so a deleted one is not an
        # empty last page -- it is a cursor that can no longer be resolved, and
        # saying so beats silently truncating the collection.
        if await db.scalar(select(id_column).where(id_column == anchor)) is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail={
                    "error": "stale_cursor",
                    "detail": "The row this cursor points at no longer exists. Start again.",
                },
            )
        stmt = stmt.where(_after(columns, descending, anchor))

    # One row more than asked for: its presence is `has_more`, and it costs a row
    # rather than the second aggregate a total would.
    rows = list((await db.execute(stmt.order_by(*sort).limit(limit + 1))).all())

    has_more = len(rows) > limit
    rows = rows[:limit]
    next_cursor = None
    if has_more and rows:
        # `.all()` always yields Row, so the primary entity is at index 0 even
        # when the select carries joined columns alongside it.
        next_cursor = encode_cursor(rows[-1][0].id)
    return rows, next_cursor, has_more
