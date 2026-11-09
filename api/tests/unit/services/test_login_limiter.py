"""Tests for the sign-in attempt limiter's window."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from api.services import login_limiter


@pytest.fixture
def clock(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(login_limiter.time, "monotonic", lambda: now[0])
    return now


def _exhaust(account: str) -> None:
    for _ in range(login_limiter.PER_ACCOUNT):
        login_limiter.check_attempt(None, account)


def test_a_new_window_starts_from_zero(clock):
    _exhaust("a@test.local")
    with pytest.raises(HTTPException) as exc:
        login_limiter.check_attempt(None, "a@test.local")
    assert exc.value.headers == {"Retry-After": "60"}

    clock[0] += 45
    with pytest.raises(HTTPException) as exc:
        login_limiter.check_attempt(None, "a@test.local")
    assert exc.value.headers == {"Retry-After": "15"}

    clock[0] += 15
    login_limiter.check_attempt(None, "a@test.local")


def test_an_attempt_without_a_source_or_account_is_not_counted(clock):
    for _ in range(login_limiter.PER_SOURCE + 1):
        login_limiter.check_attempt(None)
    assert login_limiter._counts == {}


def test_a_successful_attempt_is_handed_back(clock):
    for _ in range(login_limiter.PER_ACCOUNT * 2):
        login_limiter.check_attempt("10.0.0.1", "a@test.local").succeeded()
    assert set(login_limiter._counts.values()) == {0}


def test_an_attempt_from_an_earlier_window_hands_nothing_back(clock):
    earlier = login_limiter.check_attempt(None, "a@test.local")
    clock[0] += login_limiter.WINDOW_S
    login_limiter.check_attempt(None, "a@test.local")
    earlier.succeeded()
    assert login_limiter._counts == {("account", "a@test.local"): 1}
