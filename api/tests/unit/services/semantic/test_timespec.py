"""Time windows, which is where analytical answers most often go quietly wrong.

Every case here pins a boundary, because the boundary is the whole disagreement:
"last month" means three different windows to three different people, and each
one produces a defensible-looking number.
"""

from __future__ import annotations

from datetime import date

import pytest

from api.services.semantic.errors import SemanticError
from api.services.semantic.timespec import TimeRange, resolve

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
