"""One vocabulary for *why* a query failed.

The control plane already had a private version of this — a two-entry lookup inside
``api.metrics`` that turned an agent's error text into a ``duckhaven_query_queue_rejected``
label. The monitoring page needs the same judgement over a wider set of errors, and
two independent classifiers would eventually disagree, leaving a Grafana panel and a
UI chart telling different stories about the same failed run.

So the mapping lives here, and both read it. Reasons are deliberately coarse: each
one names a *different thing an operator would do about it*. Splitting further would
add labels nobody acts on differently, and Prometheus label cardinality is a cost
(see the cardinality policy in ``docs/operations/monitoring.md``).
"""

from __future__ import annotations

# Exact error strings the agent sends for admission-control rejections. Matched
# exactly, not by substring: these are values the agent chooses, not free text, and
# an exact match is what stops a DuckDB error that happens to mention a full queue
# from being counted as one.
ADMISSION_REJECTS = {
    "queue full": "queue_full",
    "queued timeout": "queued_timeout",
}

# A failure caused by the query itself rather than the compute running it.
SQL_ERROR = "sql_error"

# Everything else, matched as a substring against the lowercased error because these
# arrive wrapped in whatever prose the raising layer used.
_SUBSTRING_REASONS = (
    # Written by the reaper when no agent ever registered for a parked pool run.
    ("no compute became available", "no_compute"),
    # Written by bind_queued_work when an agent came up but the dispatch failed.
    ("dispatch failed after provisioning", "dispatch_failed"),
    ("out of memory", "out_of_memory"),
    # DuckDB's own phrasing when a query exceeds the configured memory limit.
    ("could not allocate", "out_of_memory"),
    # DuckDB's error classes for a problem in the query itself, not in the compute
    # running it: a typo, a missing table, a bad cast. Checked before "timeout" so an
    # invalid-input error that happens to mention a timeout stays the user's. Kept
    # apart because the fix is the author's, and on a real workload these dominate
    # and would bury the failures an operator can act on.
    ("parser error:", SQL_ERROR),
    ("binder error:", SQL_ERROR),
    ("catalog error:", SQL_ERROR),
    ("conversion error:", SQL_ERROR),
    ("invalid input error:", SQL_ERROR),
    ("constraint error:", SQL_ERROR),
    ("not implemented error:", SQL_ERROR),
    ("timeout", "timeout"),
    ("permission denied", "permission_denied"),
    ("not allowed", "permission_denied"),
)

# What the UI shows when a run failed with no error text recorded at all.
UNKNOWN = "unknown"
# Anything real but unrecognised. Kept as one bucket on purpose — an open-ended set
# of reasons drawn from raw DuckDB messages would be unbounded label cardinality.
OTHER = "error"


def classify_failure(error: str | None) -> str:
    """Map a failed query's error text to a stable reason.

    Returns ``UNKNOWN`` for an empty error and ``OTHER`` for one that matches no
    rule — never the raw text, which would be unbounded as a chart series or a
    Prometheus label.
    """
    text = (error or "").strip().lower()
    if not text:
        return UNKNOWN
    if text in ADMISSION_REJECTS:
        return ADMISSION_REJECTS[text]
    for needle, reason in _SUBSTRING_REASONS:
        if needle in text:
            return reason
    return OTHER


def is_admission_reject(reason: str) -> bool:
    """Whether a reason means the agent refused to admit the query at all.

    Keeps ``duckhaven_query_queue_rejected`` counting exactly what it counted before
    this module existed: queue saturation, not every failure.
    """
    return reason in set(ADMISSION_REJECTS.values())
