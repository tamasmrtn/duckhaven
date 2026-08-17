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
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

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
