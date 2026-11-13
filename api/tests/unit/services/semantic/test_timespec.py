"""Time windows, which is where analytical answers most often go quietly wrong.

Every case here pins a boundary, because the boundary is the whole disagreement:
"last month" means three different windows to three different people, and each
one produces a defensible-looking number.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from api.services.semantic.errors import SemanticError
from api.services.semantic.timespec import TimeRange, compare_window, resolve, today_in

# A Monday-adjacent mid-month, mid-quarter date, so week/month/quarter boundaries
# are all visibly different from each other.
TODAY = date(2026, 8, 17)  # a Monday


def window(kind, **kwargs):
    return resolve(TimeRange(kind=kind, **kwargs), today=TODAY)


def test_last_complete_month_excludes_the_month_in_progress():
    """The partial current month would drag the number down with no warning."""
    assert window("last_complete", grain="month", n=1) == (date(2026, 7, 1), date(2026, 8, 1))


def test_last_complete_month_over_several_periods():
    assert window("last_complete", grain="month", n=3) == (date(2026, 5, 1), date(2026, 8, 1))


def test_last_complete_quarter():
    assert window("last_complete", grain="quarter", n=1) == (date(2026, 4, 1), date(2026, 7, 1))


def test_last_complete_year():
    assert window("last_complete", grain="year", n=1) == (date(2025, 1, 1), date(2026, 1, 1))


def test_last_complete_week_starts_on_monday():
    """Matching DuckDB's DATE_TRUNC('week', ...), so grouping and filtering agree."""
    assert window("last_complete", grain="week", n=1) == (date(2026, 8, 10), date(2026, 8, 17))


def test_trailing_days_include_today():
    assert window("trailing", grain="day", n=30) == (date(2026, 7, 19), date(2026, 8, 18))


def test_trailing_one_day_is_today_only():
    assert window("trailing", grain="day", n=1) == (date(2026, 8, 17), date(2026, 8, 18))


def test_trailing_months_reach_back_to_a_month_start():
    assert window("trailing", grain="month", n=3) == (date(2026, 6, 1), date(2026, 8, 18))


def test_to_date_runs_from_the_period_start_to_today():
    assert window("to_date", grain="year") == (date(2026, 1, 1), date(2026, 8, 18))
    assert window("to_date", grain="month") == (date(2026, 8, 1), date(2026, 8, 18))


def test_last_complete_and_trailing_are_genuinely_different():
    """The reason both exist, and the reason neither is the default."""
    assert window("last_complete", grain="month", n=1) != window("trailing", grain="month", n=1)


def test_windows_are_half_open_so_adjacent_periods_tile():
    """No row belongs to two consecutive periods, and none falls between them."""
    july = window("last_complete", grain="month", n=1)
    june = resolve(
        TimeRange(kind="absolute", start=date(2026, 6, 1), end=date(2026, 7, 1)), today=TODAY
    )

    assert june[1] == july[0]


def test_absolute_windows_pass_through():
    assert window("absolute", start=date(2024, 1, 1), end=date(2024, 2, 1)) == (
        date(2024, 1, 1),
        date(2024, 2, 1),
    )


def test_an_absolute_window_must_move_forwards():
    with pytest.raises(SemanticError, match="strictly later"):
        window("absolute", start=date(2024, 2, 1), end=date(2024, 1, 1))


def test_an_absolute_window_needs_both_ends():
    with pytest.raises(SemanticError, match="both a start and an end"):
        window("absolute", start=date(2024, 1, 1))


def test_an_unknown_kind_lists_the_real_ones():
    with pytest.raises(SemanticError) as excinfo:
        window("since_forever", grain="month", n=1)

    assert "trailing" in str(excinfo.value)


def test_a_relative_window_needs_a_grain():
    with pytest.raises(SemanticError, match="needs a grain"):
        resolve(TimeRange(kind="trailing", n=7), today=TODAY)


def test_a_relative_window_needs_a_positive_count():
    with pytest.raises(SemanticError, match="positive number"):
        window("trailing", grain="day", n=0)


def test_month_arithmetic_crosses_a_year_boundary():
    assert resolve(
        TimeRange(kind="last_complete", grain="month", n=3), today=date(2026, 2, 10)
    ) == (date(2025, 11, 1), date(2026, 2, 1))


def test_quarter_arithmetic_crosses_a_year_boundary():
    assert resolve(
        TimeRange(kind="last_complete", grain="quarter", n=2), today=date(2026, 2, 10)
    ) == (date(2025, 7, 1), date(2026, 1, 1))


# ── Comparison windows ───────────────────────────────────────────────────────


def compared(compare, kind, *, today=TODAY, **kwargs):
    return compare_window(TimeRange(kind=kind, **kwargs), compare, today=today)


@pytest.mark.parametrize(
    ("today", "kwargs", "expected"),
    [
        # A complete month compares with the whole month before, whatever its length.
        (
            date(2026, 11, 13),
            {"kind": "last_complete", "grain": "month", "n": 1},
            (date(2026, 9, 1), date(2026, 10, 1)),
        ),
        (
            date(2026, 3, 13),
            {"kind": "last_complete", "grain": "month", "n": 1},
            (date(2026, 1, 1), date(2026, 2, 1)),
        ),
        (
            date(2026, 1, 15),
            {"kind": "last_complete", "grain": "quarter", "n": 1},
            (date(2025, 7, 1), date(2025, 10, 1)),
        ),
        (
            date(2026, 11, 13),
            {"kind": "last_complete", "grain": "month", "n": 3},
            (date(2026, 5, 1), date(2026, 8, 1)),
        ),
        # A to-date window compares the same elapsed span of the period before...
        (
            date(2026, 11, 13),
            {"kind": "to_date", "grain": "month"},
            (date(2026, 10, 1), date(2026, 10, 14)),
        ),
        (
            date(2026, 5, 31),
            {"kind": "to_date", "grain": "quarter"},
            (date(2026, 1, 1), date(2026, 3, 3)),
        ),
        # ...but never runs past that period's end.
        (
            date(2026, 3, 31),
            {"kind": "to_date", "grain": "month"},
            (date(2026, 2, 1), date(2026, 3, 1)),
        ),
        # A trailing window compares with the same span just before it.
        (
            date(2026, 11, 13),
            {"kind": "trailing", "grain": "day", "n": 30},
            (date(2026, 9, 15), date(2026, 10, 15)),
        ),
        (
            date(2026, 11, 13),
            {"kind": "trailing", "grain": "month", "n": 3},
            (date(2026, 6, 1), date(2026, 8, 14)),
        ),
        # An absolute window compares with the same number of days before it.
        (
            TODAY,
            {"kind": "absolute", "start": date(2026, 1, 10), "end": date(2026, 1, 20)},
            (date(2025, 12, 31), date(2026, 1, 10)),
        ),
    ],
)
def test_previous_period(today, kwargs, expected):
    assert compared("previous_period", today=today, **kwargs) == expected


@pytest.mark.parametrize(
    ("today", "kwargs", "expected"),
    [
        (
            date(2026, 11, 13),
            {"kind": "last_complete", "grain": "month", "n": 1},
            (date(2025, 10, 1), date(2025, 11, 1)),
        ),
        # 29 February has no twin a year earlier, so it compares with the 28th.
        (
            date(2028, 2, 29),
            {"kind": "to_date", "grain": "year"},
            (date(2027, 1, 1), date(2027, 3, 1)),
        ),
        (
            TODAY,
            {"kind": "absolute", "start": date(2028, 2, 29), "end": date(2028, 3, 1)},
            (date(2027, 2, 28), date(2027, 3, 1)),
        ),
    ],
)
def test_previous_year(today, kwargs, expected):
    assert compared("previous_year", today=today, **kwargs) == expected


def test_a_comparison_never_overlaps_the_window_it_compares():
    """Overlap would count the same rows on both sides of a delta."""
    windows = (
        [
            {"kind": "last_complete", "grain": g, "n": n}
            for g in ("week", "month", "quarter", "year")
            for n in (1, 3)
        ]
        + [
            {"kind": "trailing", "grain": "day", "n": 30},
            {"kind": "trailing", "grain": "month", "n": 3},
        ]
        + [{"kind": "to_date", "grain": g} for g in ("week", "month", "quarter", "year")]
    )
    day = date(2024, 1, 1)
    while day < date(2027, 1, 1):
        for w in windows:
            start, end = resolve(TimeRange(**w), today=day)
            for compare in ("previous_period", "previous_year"):
                if compare == "previous_year" and end - start > timedelta(days=366):
                    continue  # refused; see the next test
                prev_start, prev_end = compared(compare, today=day, **w)
                assert prev_start < prev_end <= start, (day, w, compare)
        day += timedelta(days=5)


def test_a_window_longer_than_a_year_has_no_previous_year():
    """The same dates a year earlier would overlap it."""
    with pytest.raises(SemanticError, match="previous_period"):
        compared("previous_year", "last_complete", grain="year", n=3)


def test_an_unknown_comparison_lists_the_real_ones():
    with pytest.raises(SemanticError) as exc:
        compared("previous_decade", "last_complete", grain="month", n=1)
    assert set(exc.value.alternatives) == {"previous_period", "previous_year"}


# ── Today, in the workspace's time zone ──────────────────────────────────────


@pytest.mark.parametrize(
    ("now", "timezone", "expected"),
    [
        (datetime(2026, 11, 13, 12, tzinfo=UTC), "UTC", date(2026, 11, 13)),
        (datetime(2026, 11, 13, 12, tzinfo=UTC), "Pacific/Kiritimati", date(2026, 11, 14)),
        (datetime(2026, 11, 13, 12, tzinfo=UTC), "Pacific/Pago_Pago", date(2026, 11, 13)),
        (datetime(2026, 11, 13, 1, tzinfo=UTC), "America/New_York", date(2026, 11, 12)),
        # Either side of the end of British Summer Time (01:00 UTC, 25 October):
        # 23:30 UTC is past midnight in London before it, and not after.
        (datetime(2026, 10, 24, 23, 30, tzinfo=UTC), "Europe/London", date(2026, 10, 25)),
        (datetime(2026, 10, 25, 23, 30, tzinfo=UTC), "Europe/London", date(2026, 10, 25)),
    ],
)
def test_today_is_the_date_in_the_workspace_time_zone(now, timezone, expected):
    assert today_in(timezone, now=now) == expected
