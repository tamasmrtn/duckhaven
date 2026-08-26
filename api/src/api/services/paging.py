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
import json
import uuid
from datetime import datetime
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import Select, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute


def _encode_value(value: Any) -> Any:
    return value.isoformat() if isinstance(value, datetime) else str(value)


def _decode_value(raw: Any, column: InstrumentedAttribute) -> Any:
    python_type = column.type.python_type
    if python_type is datetime:
        return datetime.fromisoformat(raw)
    if python_type is uuid.UUID:
        return uuid.UUID(raw)
    if python_type is int:
        return int(raw)
    return raw


def encode_cursor(values: list[Any]) -> str:
    """Opaque cursor naming the last row of a page by its whole sort key.

    The values must be the ones the database holds, read off the row rather than
    recomputed: a value that rounds differently from the stored one puts a row on
    both sides of the split.
    """
    return base64.urlsafe_b64encode(
        json.dumps([_encode_value(v) for v in values]).encode()
    ).decode()


def decode_cursor(cursor: str, columns: list[InstrumentedAttribute]) -> list[Any]:
    """Reverse :func:`encode_cursor`, or 422.

    A malformed cursor is a bad request, not a server fault, and ignoring it
    would quietly hand back the whole collection instead of a page.
    """
    try:
        raw = json.loads(base64.urlsafe_b64decode(cursor.encode()).decode())
        if not isinstance(raw, list) or len(raw) != len(columns):
            raise ValueError("wrong shape")
        return [_decode_value(v, c) for v, c in zip(raw, columns, strict=True)]
    except (ValueError, TypeError, binascii.Error, UnicodeDecodeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"error": "invalid_cursor", "detail": "Malformed cursor."},
        ) from exc


def _after(columns: list[InstrumentedAttribute], values: list[Any], descending: bool):
    """Rows strictly after ``values`` in the given ordering.

    Spelled as nested ORs rather than a row-value comparison: SQLite does not
    evaluate ``(a, b) > (x, y)`` against these column types, and the unit suite
    runs on SQLite while production runs on Postgres. These collections are small
    enough that the index shape does not pay for a dialect branch -- the query
    history keeps the row-value form where it matters.
    """
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

    ``sort_columns`` is the full ordering, ending in a column unique per row --
    without that tiebreak the order is not total, and rows sharing a value can be
    served twice or not at all. ``stmt`` must carry its filters but neither ORDER
    BY nor LIMIT; both are applied here so the ordering and the cursor cannot
    disagree.

    Returns the rows, the cursor for the next page, and whether one exists.
    """
    if cursor is not None:
        stmt = stmt.where(_after(sort_columns, decode_cursor(cursor, sort_columns), descending))

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
        next_cursor = encode_cursor([getattr(entity, c.key) for c in sort_columns])
    return rows, next_cursor, has_more
