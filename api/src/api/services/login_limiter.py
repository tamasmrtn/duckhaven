"""Bounds on password sign-in attempts, per source address and per account.

Counted in fixed one-minute windows, in process: each API replica counts the
attempts it serves, so behind a load balancer the deployment-wide ceiling is the
replica count times these limits. An attempt is counted before any database or
password work, so concurrent requests cannot pass the check together, and handed
back when the sign-in succeeds: the limits bound failed attempts, not sign-ins.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass

from fastapi import HTTPException, status

WINDOW_S = 60.0
PER_SOURCE = 20
PER_ACCOUNT = 10

_window = 0
_window_started = 0.0
_counts: dict[tuple[str, str], int] = {}


@dataclass(frozen=True)
class Attempt:
    window: int
    keys: tuple[tuple[str, str], ...]

    def succeeded(self) -> None:
        """Hand the attempt back: a successful sign-in counts against no limit."""
        if self.window != _window:
            return
        for key in self.keys:
            if _counts.get(key, 0) > 0:
                _counts[key] -= 1


def check_attempt(source: str | None, account: str | None = None) -> Attempt:
    """Count one sign-in attempt; raise 429 when its source or account is over the limit."""
    global _window, _window_started
    now = time.monotonic()
    if now - _window_started >= WINDOW_S:
        # Starting a fresh window also bounds memory to one window's keys.
        _window += 1
        _window_started = now
        _counts.clear()

    limits: list[tuple[tuple[str, str], int]] = []
    if source:
        limits.append((("source", source), PER_SOURCE))
    if account:
        limits.append((("account", account.strip().lower()), PER_ACCOUNT))

    over = False
    for key, limit in limits:
        _counts[key] = _counts.get(key, 0) + 1
        over = over or _counts[key] > limit
    if over:
        retry_after = max(1, math.ceil(WINDOW_S - (now - _window_started)))
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "error": "too_many_sign_in_attempts",
                "detail": f"Too many sign-in attempts. Try again in {retry_after} seconds.",
            },
            headers={"Retry-After": str(retry_after)},
        )
    return Attempt(window=_window, keys=tuple(key for key, _ in limits))


def reset() -> None:
    """Forget every count. For tests."""
    global _window_started
    _window_started = 0.0
    _counts.clear()
