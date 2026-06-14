"""Agent concurrency profiles and the worksheet ``SET`` control command.

Shared so the API (which parses the command before dispatch) and the agent
(which applies it) agree on the valid profile names. A profile is an ordered
list of integer slot weights, largest first: the agent's memory budget is split
across the slots in proportion to the weights, and a new query takes the largest
free slot (the rest queue). ``decaying_3`` => the first running query gets the
most, the second a little less, the third least.
"""

from __future__ import annotations

import re

# Profile name -> descending slot weights. Slot count = number of weights.
# ``auto`` has no fixed ladder (empty weights): the agent sizes each reservation
# from the query's EXPLAIN estimate and snaps it to a BUCKET_FRACTIONS bucket.
CONCURRENCY_PROFILES: dict[str, list[int]] = {
    "auto": [],
    "single": [1],
    "equal_2": [1, 1],
    "decaying_2": [2, 1],
    "decaying_3": [3, 2, 1],
}
DEFAULT_PROFILE = "auto"

# T-shirt buckets for the ``auto`` profile, as fractions of the agent's memory
# budget. An estimate snaps UP to the smallest bucket that fits (see
# ``agent.executor.estimator.bucket_for``).
BUCKET_FRACTIONS: dict[str, float] = {
    "XS": 1 / 12,
    "S": 1 / 6,
    "M": 1 / 3,
    "L": 2 / 3,
    "XL": 1.0,
}

# `SET duckhaven_concurrency = '<profile>'` (quotes optional) or
# `RESET duckhaven_concurrency` (-> the default). Case-insensitive, tolerant of
# surrounding whitespace and a trailing semicolon. This is a DuckHaven control
# command intercepted by the control plane; it never reaches DuckDB.
_SET_RE = re.compile(
    r"^\s*set\s+duckhaven_concurrency\s*=\s*'?(?P<profile>\w+)'?\s*;?\s*$",
    re.IGNORECASE,
)
_RESET_RE = re.compile(r"^\s*reset\s+duckhaven_concurrency\s*;?\s*$", re.IGNORECASE)


def parse_set_concurrency(sql: str) -> str | None:
    """Return the target profile name if ``sql`` is the concurrency command.

    Returns ``None`` when ``sql`` is an ordinary query. Raises ``ValueError`` for
    the command with an unknown profile name so the caller can surface a clear
    error instead of silently dispatching invalid SQL.
    """
    if _RESET_RE.match(sql):
        return DEFAULT_PROFILE
    match = _SET_RE.match(sql)
    if match is None:
        return None
    profile = match.group("profile").lower()
    if profile not in CONCURRENCY_PROFILES:
        valid = ", ".join(CONCURRENCY_PROFILES)
        raise ValueError(f"Unknown concurrency profile '{profile}'. Valid: {valid}.")
    return profile
