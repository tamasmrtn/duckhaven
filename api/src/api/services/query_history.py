"""Filtering, sorting and keyset pagination for the query-history list.

Kept out of the router so the SQL shapes here — which are fiddly in a way the
rest of the endpoint is not — can be read and tested on their own.

**Why keyset and not offset.** History is appended to continuously by
interactive runs, SQL sessions, the scheduler and the maintenance scanner. Under
``OFFSET`` a row inserted between two page requests shifts every later page by
one, so the reader sees a row twice or never sees it at all. A cursor anchored to
the last row of the previous page is immune to that. The rows endpoint's
stringified-offset cursor (``RowsPageOut``) is not a precedent worth copying
here: it pages a frozen Parquet file, which cannot grow underneath it.
"""

from __future__ import annotations

import base64
import binascii
import re
import uuid
from datetime import datetime
from typing import Literal

from sqlalchemy import ColumnElement, Integer, String, and_, cast, func, or_, tuple_
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.sql.expression import FunctionElement

from api.models.query import Query

SortKey = Literal["started_at", "duration"]
SortDir = Literal["asc", "desc"]

# The five states a run can be in. Mirrors the QueryStatus union the frontend
# declares in web/src/types/query.ts; there is no enum on the column itself.
QUERY_STATUSES: frozenset[str] = frozenset({"queued", "running", "done", "failed", "cancelled"})

# Origins that are machinery rather than someone's work.
#
# "maintenance" is the Lakehouse-health scanner's per-table `SELECT 1` probe
# (services/maintenance/scanner.py). It writes one row per scanned table per
# cycle with a null user_id, which floods History the moment anyone widens the
# scope past their own runs. It is excluded for the same reason "sample" and
# "metadata" are: nobody asked for it to run.
#
# Deliberately *not* excluded: origin="session". Those are dbt and dlt
# statements — real user work that happens not to have been typed into a
# worksheet.
HIDDEN_ORIGINS: tuple[str, ...] = ("sample", "metadata", "maintenance")

# A full UUID, or the leading part of one. The UI truncates ids through
# `shortId` to 8 characters, so people paste prefixes.
_ID_PREFIX_RE = re.compile(r"^[0-9a-fA-F-]{1,36}$")


class InvalidCursor(ValueError):
    """The cursor did not decode, or does not match the requested sort."""


class _DurationMs(FunctionElement):
    """Wall-clock fallback for a run whose agent never reported a duration.

    ``finished_at - started_at`` in milliseconds. Spelled per-dialect because
    Postgres wants ``EXTRACT(EPOCH ...)`` and SQLite (the unit-test database)
    has no such thing.
    """

    inherit_cache = True
    type = Integer()


@compiles(_DurationMs, "postgresql")
def _duration_ms_pg(element, compiler, **kw) -> str:
    finished, started = list(element.clauses)
    return (
        f"(EXTRACT(EPOCH FROM ({compiler.process(finished, **kw)} - "
        f"{compiler.process(started, **kw)})) * 1000)"
    )


@compiles(_DurationMs, "sqlite")
@compiles(_DurationMs)
def _duration_ms_sqlite(element, compiler, **kw) -> str:
    finished, started = list(element.clauses)
    return (
        f"((julianday({compiler.process(finished, **kw)}) - "
        f"julianday({compiler.process(started, **kw)})) * 86400000)"
    )


def duration_expr() -> ColumnElement[int]:
    """The duration History filters and sorts on, in milliseconds.

    ``Query.duration_ms`` is the agent's *execution* time and is null for a run
    that failed before the agent could report one. Filtering on it alone would
    therefore drop every failure — exactly backwards, since a query that hung
    for two minutes and then died is the thing a slow-query search is looking
    for. Fall back to wall clock when the agent reported nothing.

    Null when the run has not finished: a running query's duration is unknown,
    not zero, and pretending otherwise would sort it against completed runs.
    """
    return func.coalesce(
        Query.duration_ms,
        cast(_DurationMs(Query.finished_at, Query.started_at), Integer),
    )


def _escape_like(term: str) -> str:
    """Neutralize LIKE metacharacters so a search term matches literally.

    Backslash first, or it would double-escape the escapes added after it.
    """
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def encode_cursor(row_id: uuid.UUID, value: datetime | int | None) -> str:
    """Opaque cursor naming the last row of a page.

    ``value`` must be the sort key as the *database* computed it, selected
    alongside the row — never recomputed in Python. The duration expression
    rounds when it casts, so a Python-side recomputation can land a millisecond
    away from what the comparison in :func:`keyset_predicate` sees, and a row
    whose stored value is one below its own cursor is returned on the next page
    as well. That bug is invisible until two pages happen to split on it.
    """
    if value is None:
        raw = "null"
    elif isinstance(value, datetime):
        raw = value.isoformat()
    else:
        raw = str(int(value))
    return base64.urlsafe_b64encode(f"{raw}|{row_id}".encode()).decode()


def decode_cursor(cursor: str, sort: SortKey) -> tuple[datetime | int | None, uuid.UUID]:
    """Reverse :func:`encode_cursor`, or raise :class:`InvalidCursor`.

    A malformed cursor is rejected rather than silently treated as "start from
    the beginning": a client that got its paging wrong should be told so, not
    handed page one forever while appearing to loop.
    """
    try:
        decoded = base64.urlsafe_b64decode(cursor.encode()).decode()
        raw, _, raw_id = decoded.rpartition("|")
        row_id = uuid.UUID(raw_id)
    except (ValueError, binascii.Error, UnicodeDecodeError) as exc:
        raise InvalidCursor("Malformed cursor") from exc

    if sort == "duration":
        if raw == "null":
            return None, row_id
        try:
            return int(raw), row_id
        except ValueError as exc:
            raise InvalidCursor("Cursor does not match sort=duration") from exc
    try:
        return datetime.fromisoformat(raw), row_id
    except ValueError as exc:
        raise InvalidCursor("Cursor does not match sort=started_at") from exc


def order_by(sort: SortKey, direction: SortDir) -> list[ColumnElement]:
    """Ordering clauses, always with ``id`` as the deterministic tiebreaker.

    Without the tiebreaker two runs sharing a timestamp — common, since a
    session dispatches statements in a burst — could come back in either order,
    and a cursor anchored to one of them would skip or repeat the other.

    Duration sorts nulls last in *both* directions, so runs whose duration is
    unknown never head a "slowest first" list.
    """
    if sort == "duration":
        column = duration_expr()
        if direction == "asc":
            return [column.asc().nulls_last(), Query.id.asc()]
        return [column.desc().nulls_last(), Query.id.desc()]
    if direction == "asc":
        return [Query.started_at.asc(), Query.id.asc()]
    return [Query.started_at.desc(), Query.id.desc()]


def keyset_predicate(
    sort: SortKey, direction: SortDir, value: datetime | int | None, row_id: uuid.UUID
) -> ColumnElement[bool]:
    """Rows strictly after ``(value, row_id)`` in the given ordering."""
    if sort == "started_at":
        # Row-value form: Postgres turns this into a range scan over
        # ix_queries_workspace_started_id, which the OR-form spelling below
        # would not get.
        anchor = (value, row_id)
        if direction == "asc":
            return tuple_(Query.started_at, Query.id) > anchor
        return tuple_(Query.started_at, Query.id) < anchor

    column = duration_expr()
    if value is None:
        # Already in the nulls-last tail: only other null-duration rows remain,
        # separated from this one by id alone.
        after_id = Query.id > row_id if direction == "asc" else Query.id < row_id
        return and_(column.is_(None), after_id)
    if direction == "asc":
        return or_(column > value, and_(column == value, Query.id > row_id), column.is_(None))
    return or_(column < value, and_(column == value, Query.id < row_id), column.is_(None))


def search_predicate(term: str) -> ColumnElement[bool]:
    """Case-insensitive substring match on the statement text.

    A plain ILIKE over a set already narrowed by workspace, time and scope. No
    trigram index, no tsvector: at the volumes DuckHaven deployments carry that
    machinery would cost more to maintain than it saves, and it can be added
    behind this same call when a real deployment shows it is needed.
    """
    return Query.sql.ilike(f"%{_escape_like(term)}%", escape="\\")


def id_predicate(value: str) -> ColumnElement[bool]:
    """Match a full query id, or the leading part of one.

    Composed with the caller's workspace and permission predicates by the
    endpoint, never short-circuiting them — knowing an id must not be a way to
    read another workspace's run.
    """
    if not _ID_PREFIX_RE.match(value):
        raise ValueError("Query id must be a UUID or the start of one")
    try:
        return Query.id == uuid.UUID(value)
    except ValueError:
        # A prefix: compare against the id's text form. Postgres renders a uuid
        # lowercase and hyphenated, so normalize the needle to match.
        return cast(Query.id, String).like(f"{value.lower()}%")
