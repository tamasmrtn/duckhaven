from datetime import UTC, datetime

import pytest

from api.services.scheduler.cron import next_run, validate_cron


def test_validate_cron_accepts_standard_expressions():
    validate_cron("0 2 * * *")  # daily at 02:00
    validate_cron("*/5 * * * *")  # every 5 minutes
    validate_cron("0 9 * * 1-5")  # weekdays at 09:00


@pytest.mark.parametrize("expr", ["", "not a cron", "0 2 * *", "61 * * * *", "* * * * 8"])
def test_validate_cron_rejects_bad_expressions(expr):
    with pytest.raises(ValueError):
        validate_cron(expr)


def test_next_run_is_strictly_after():
    now = datetime(2026, 6, 29, 1, 30, tzinfo=UTC)
    nxt = next_run("0 2 * * *", now)
    assert nxt == datetime(2026, 6, 29, 2, 0, tzinfo=UTC)
    assert nxt > now


def test_next_run_crosses_day_boundary():
    now = datetime(2026, 6, 29, 3, 0, tzinfo=UTC)
    # Daily at 02:00 — the next occurrence is tomorrow.
    assert next_run("0 2 * * *", now) == datetime(2026, 6, 30, 2, 0, tzinfo=UTC)


def test_next_run_crosses_week_boundary():
    # Sunday 2026-06-28; weekdays-only at 09:00 should jump to Monday.
    sunday = datetime(2026, 6, 28, 10, 0, tzinfo=UTC)
    assert next_run("0 9 * * 1-5", sunday) == datetime(2026, 6, 29, 9, 0, tzinfo=UTC)


def test_next_run_validates_expression():
    with pytest.raises(ValueError):
        next_run("bogus", datetime(2026, 6, 29, tzinfo=UTC))
