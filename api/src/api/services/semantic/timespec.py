"""Time windows, resolved to concrete dates.

"Last month" is the single most reliable way to get a wrong analytical answer,
and it is wrong in two independent ways. The first is *which column* — solved by
binding each metric to its own time dimension. The second is *which window*: to
one person "last month" is the previous calendar month, to another it is the
trailing thirty days, and to a third it is month-to-date. Those are three
different numbers and the phrase does not distinguish them.

So the vocabulary here is explicit and small, and the assistant has to pick one
rather than say "last month" and hope. There is no default.

Windows resolve to concrete dates rather than to ``CURRENT_DATE`` arithmetic. The
compiled SQL is stored in query history and read back later during an
investigation, and a query that means something different every time it is re-run
is not a record of what was asked. It also makes the compiler a pure function of
its inputs, which is what makes it testable.

All windows are half-open, ``start <= t < end``. Half-open is what makes adjacent
periods tile without double-counting the boundary row — the off-by-one that
inflates a daily series by one day's worth at each end.

"Today" is the date in the workspace's time zone, not the server's: at 23:00 in
New York a UTC server is already on tomorrow, and "month to date" on the 1st
would quietly become last month.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from api.services.semantic.errors import SemanticError

GRAINS = ("day", "week", "month", "quarter", "year")

# What each window kind means, in the words the assistant sees. Kept here so the
# tool docstring and the error messages cannot drift from the implementation.
WINDOW_KINDS = {
    "last_complete": (
        "the N most recent *complete* periods, excluding the one in progress "
        "(last_complete/month/1 = the whole of last calendar month)"
    ),
    "trailing": (
        "a rolling window of N periods ending today, including today "
        "(trailing/day/30 = the last 30 days)"
    ),
    "to_date": (
        "from the start of the current period up to and including today "
        "(to_date/year = year to date)"
    ),
    "absolute": "an explicit start and end date; end is exclusive",
}


# What each comparison means, in the words the assistant sees.
COMPARE_KINDS = {
    "previous_period": (
        "the same span just before the window: the month before a complete month, "
        "the same number of days into last month for month to date"
    ),
    "previous_year": ("the same dates one year earlier; 29 February compares with 28 February"),
}


@dataclass(frozen=True)
class TimeRange:
    """A requested window, before resolution."""

    kind: str
    grain: str | None = None
    n: int | None = None
    start: date | None = None
    end: date | None = None


def _start_of(d: date, grain: str) -> date:
    if grain == "day":
        return d
    if grain == "week":
        # Monday, matching DuckDB's DATE_TRUNC('week', ...).
        return d - timedelta(days=d.weekday())
    if grain == "month":
        return d.replace(day=1)
    if grain == "quarter":
        return d.replace(month=((d.month - 1) // 3) * 3 + 1, day=1)
    if grain == "year":
        return d.replace(month=1, day=1)
    raise SemanticError(f"Unknown time grain {grain!r}.", alternatives=list(GRAINS))


def _shift(d: date, grain: str, periods: int) -> date:
    """Move a period-aligned date by whole periods."""
    if grain == "day":
        return d + timedelta(days=periods)
    if grain == "week":
        return d + timedelta(weeks=periods)
    if grain in ("month", "quarter"):
        step = 1 if grain == "month" else 3
        total = (d.year * 12 + (d.month - 1)) + periods * step
        return date(total // 12, total % 12 + 1, 1)
    if grain == "year":
        return d.replace(year=d.year + periods)
    raise SemanticError(f"Unknown time grain {grain!r}.", alternatives=list(GRAINS))


def _utcnow() -> datetime:
    return datetime.now(UTC)


def today_in(timezone: str, *, now: datetime | None = None) -> date:
    """Today's date in an IANA time zone, which the caller has already validated."""
    return (now or _utcnow()).astimezone(ZoneInfo(timezone)).date()


def _year_earlier(d: date) -> date:
    """The same date a year earlier; 29 February, which has none, becomes the 28th."""
    try:
        return d.replace(year=d.year - 1)
    except ValueError:
        return d.replace(year=d.year - 1, day=28)


def compare_window(window: TimeRange, compare: str, *, today: date) -> tuple[date, date]:
    """The window a comparison measures against, half-open like :func:`resolve`.

    A comparison never overlaps the window it is compared with, so no row counts
    on both sides of a delta.
    """
    start, end = resolve(window, today=today)
    if compare == "previous_year":
        if end - start > timedelta(days=366):
            raise SemanticError(
                "A window longer than a year overlaps the same dates a year earlier; "
                "compare it with previous_period instead."
            )
        return _year_earlier(start), _year_earlier(end - timedelta(days=1)) + timedelta(days=1)
    if compare != "previous_period":
        raise SemanticError(f"Unknown comparison {compare!r}.", alternatives=list(COMPARE_KINDS))

    if window.kind == "absolute":
        return start - (end - start), start

    # Relative windows start on a period boundary, so whole periods move them.
    grain = window.grain
    periods = 1 if window.kind == "to_date" else window.n
    current = _start_of(today, grain)
    if end <= current:
        # Only complete periods: the same number of complete periods before.
        return _shift(start, grain, -periods), _shift(end, grain, -periods)
    # The window runs into the period in progress. Compare the same number of
    # days into the matching earlier period, but never past that period's end.
    earlier = _shift(current, grain, -periods)
    return _shift(start, grain, -periods), min(earlier + (end - current), _shift(earlier, grain, 1))


def resolve(window: TimeRange, *, today: date) -> tuple[date, date]:
    """Resolve a window to a half-open ``[start, end)`` pair of dates."""
    kind = window.kind

    if kind == "absolute":
        if window.start is None or window.end is None:
            raise SemanticError("An absolute window needs both a start and an end date.")
        if window.end <= window.start:
            raise SemanticError(
                f"The window ends ({window.end}) on or before it starts ({window.start}); "
                "end is exclusive, so it must be strictly later."
            )
        return window.start, window.end

    if kind not in WINDOW_KINDS:
        raise SemanticError(f"Unknown time window kind {kind!r}.", alternatives=list(WINDOW_KINDS))

    grain = window.grain
    if grain not in GRAINS:
        raise SemanticError(f"A {kind!r} window needs a grain.", alternatives=list(GRAINS))

    if kind == "to_date":
        # Inclusive of today, so the exclusive end is tomorrow.
        return _start_of(today, grain), today + timedelta(days=1)

    n = window.n
    if n is None or n < 1:
        raise SemanticError(f"A {kind!r} window needs a positive number of periods.")

    current = _start_of(today, grain)
    if kind == "last_complete":
        # Ends where the in-progress period begins, so the partial period that
        # would drag the most recent value down is excluded.
        return _shift(current, grain, -n), current
    # trailing: n periods back from the start of the current one, through today.
    return _shift(current, grain, -(n - 1)), today + timedelta(days=1)
