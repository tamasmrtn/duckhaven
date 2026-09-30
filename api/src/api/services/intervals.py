"""Exact interval arithmetic for the monitoring page.

The page used to describe activity with point samples and bucket flags, and both
lie in ways a reader can't see: a 2 s sample misses anything shorter, and "share of
buckets with any activity" changes with the bucket size (the same agent read 0 % and
67 % busy depending on the zoom). Everything here works on exact half-open intervals
``[start, end)`` in epoch seconds instead, which is what makes the results additive
across buckets and therefore identical at every zoom level:

* ``spread`` distributes interval-seconds over buckets. Divided by a bucket's length
  it is the time-weighted average concurrency (Little's law).
* ``merge``/``intersect`` give the measure of a union ("busy" = time at least one
  query was running) and restrict it to spans (time the agent was up).
* ``sweep`` does all of that for many overlapping intervals in one pass: per bucket,
  the interval-seconds (concurrency), the seconds with at least one open (busy) and
  the true peak, counting only inside given spans (e.g. while the agent was up).

Pure functions, no database: they are tested on their own.
"""

from __future__ import annotations

import bisect
import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

Interval = tuple[float, float]

# A zero-length run (a query that started and finished in the same instant) still
# happened; it is widened to this so it counts once towards a peak.
_MIN_RUN_S = 0.001


def merge(intervals: Iterable[Interval]) -> list[Interval]:
    """Sorted, non-overlapping union of ``intervals``. Touching intervals join."""
    out: list[Interval] = []
    for start, end in sorted(i for i in intervals if i[1] > i[0]):
        if out and start <= out[-1][1]:
            if end > out[-1][1]:
                out[-1] = (out[-1][0], end)
        else:
            out.append((start, end))
    return out


def intersect(a: Sequence[Interval], b: Sequence[Interval]) -> list[Interval]:
    """Intersection of two *merged* interval lists."""
    out: list[Interval] = []
    i = j = 0
    while i < len(a) and j < len(b):
        start = max(a[i][0], b[j][0])
        end = min(a[i][1], b[j][1])
        if end > start:
            out.append((start, end))
        if a[i][1] < b[j][1]:
            i += 1
        else:
            j += 1
    return out


def measure(intervals: Iterable[Interval]) -> float:
    """Total length of already-disjoint intervals."""
    return sum(end - start for start, end in intervals)


def spread(intervals: Iterable[Interval], edges: Sequence[float], end: float) -> list[float]:
    """Seconds of ``intervals`` falling in each bucket.

    Buckets are ``[edges[i], edges[i+1])``, the last one ending at ``end``. Overlapping
    intervals are *not* merged first: two queries running side by side contribute two
    seconds per second, which is what an average concurrency needs.
    """
    totals = [0.0] * len(edges)
    if not edges:
        return totals
    first = edges[0]
    for start, stop in intervals:
        start, stop = max(start, first), min(stop, end)
        if stop <= start:
            continue
        i = bisect.bisect_right(edges, start) - 1
        while i < len(edges) and start < stop:
            bucket_end = edges[i + 1] if i + 1 < len(edges) else end
            piece = min(stop, bucket_end) - start
            if piece > 0:
                totals[i] += piece
            start = bucket_end
            i += 1
    return totals


@dataclass
class Sweep:
    """Per-bucket results of :func:`sweep`."""

    seconds: list[float]  # interval-seconds: divide by bucket length for concurrency
    covered: list[float]  # seconds with at least one interval open
    peak: list[int]  # most intervals open at any instant


def sweep(
    intervals: Iterable[Interval],
    gate: Sequence[Interval],
    edges: Sequence[float],
    end: float,
    *,
    widen_instants: bool = False,
) -> Sweep:
    """Integrate many overlapping ``intervals`` over the buckets in one pass.

    Only time inside the *merged* ``gate`` spans counts: a query row still marked
    running after its agent went away stops counting where the agent stopped.

    A sweep line over sorted start/end events: at equal timestamps an end is taken
    before a start, so back-to-back intervals (one ends as the next begins) never
    count as concurrent. The open count carries across bucket edges, so an interval
    spanning three buckets counts in all three. With ``widen_instants`` a zero-length
    interval (a query that ran in no measurable time) is widened to a millisecond so
    it still registers in the peak; without it, it is ignored (a wait of zero).
    """
    n = len(edges)
    result = Sweep([0.0] * n, [0.0] * n, [0] * n)
    if not n or not gate:
        return result
    first = edges[0]
    # (time, order, delta): order 0 = interval end, 1 = gate change, 2 = interval start.
    events: list[tuple[float, int, int]] = []
    level = 0
    for start, stop in intervals:
        if widen_instants:
            stop = max(stop, start + _MIN_RUN_S)
        if stop <= first or start >= end or stop <= start:
            continue
        if start <= first:
            level += 1
        else:
            events.append((start, 2, 1))
        if stop < end:
            events.append((stop, 0, -1))
    gate_open = False
    for g_start, g_stop in gate:
        if g_stop <= first or g_start >= end:
            continue
        if g_start <= first:
            gate_open = True
        else:
            events.append((g_start, 1, 1))
        if g_stop < end:
            events.append((g_stop, 1, -1))
    events.sort()

    seconds, covered, peak = result.seconds, result.covered, result.peak
    i = 0
    cursor = first
    bucket_end = edges[1] if n > 1 else end
    peak[0] = level if gate_open else 0

    def advance(to: float) -> None:
        nonlocal i, cursor, bucket_end
        while cursor < to:
            piece_end = min(to, bucket_end)
            if gate_open and level:
                span = piece_end - cursor
                seconds[i] += level * span
                covered[i] += span
            cursor = piece_end
            if cursor >= bucket_end and i + 1 < n:
                i += 1
                bucket_end = edges[i + 1] if i + 1 < n else end
                peak[i] = level if gate_open else 0

    for at, order, delta in events:
        advance(at)
        if order == 1:
            gate_open = delta > 0
        else:
            level += delta
        if delta < 0 and at == edges[i]:
            # Ends (and a gate closing) exactly on this bucket's first instant happen
            # before anything in it: the bucket's peak starts from the level after them.
            peak[i] = level if gate_open else 0
        elif gate_open and level > peak[i]:
            peak[i] = level
    advance(end)
    return result


def nearest_rank(values: Sequence[float], quantile: float) -> float | None:
    """The nearest-rank percentile: an actually observed value, never interpolated."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(quantile * len(ordered)))
    return ordered[rank - 1]
