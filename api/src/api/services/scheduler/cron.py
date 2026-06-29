"""Cron parsing helpers, wrapping ``croniter``.

Standard 5-field cron expressions (minute hour day-of-month month day-of-week),
evaluated in UTC. Kept tiny and dependency-isolated so the scheduler and the API
router share one source of truth for validation and next-run computation.
"""

from __future__ import annotations

from datetime import datetime

from croniter import croniter


def validate_cron(expr: str) -> None:
    """Raise ``ValueError`` if ``expr`` is not a valid 5-field cron expression."""
    if not croniter.is_valid(expr):
        raise ValueError(f"Invalid cron expression: {expr!r}")


def next_run(expr: str, after: datetime) -> datetime:
    """The next UTC occurrence of ``expr`` strictly after ``after``.

    ``after`` should be timezone-aware (UTC); croniter returns an aware datetime
    in the same tzinfo, so the result is directly comparable to ``next_run_at``.
    """
    validate_cron(expr)
    return croniter(expr, after).get_next(datetime)
