"""When a relationship was observed, and when that stops meaning much.

Two small concerns that the rest of the lineage code kept re-deriving:

**Timezone normalisation.** Timestamps come back naive from SQLite and aware
from Postgres. Every comparison in the lineage code touches both — the write
path compares an incoming observation against a stored one, the read path merges
several stored ones — so the normalisation lives here rather than being copied
into each.

**Freshness.** A relationship is *stale* when no producer has re-asserted it
recently. That is deliberately a claim about confirmation, not about truth: a
table built once a year has perfectly correct lineage that nothing will confirm
again for eleven months. Staleness says "nobody has said this lately", which is
what a reader needs in order to decide how hard to check, and it is why stale
edges are marked rather than removed.

The threshold is one number (``LINEAGE_STALE_AFTER_DAYS``) applied to every
producer alike, but it is applied to each producer's *own* observation, because
that is where the useful distinction lives: an import that stopped running last
quarter is stale even though a query confirmed the same pair this morning.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta


def aware_utc(value: datetime) -> datetime:
    """``value`` as an aware UTC datetime, assuming UTC when it carries no zone.

    Naive values only ever reach here from SQLite, which drops the offset it was
    given; everything DuckHaven writes is UTC, so the assumption is safe.
    """
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def is_stale(last_seen_at: datetime, *, now: datetime, after_days: int) -> bool:
    """Whether nothing has re-asserted this observation within ``after_days``.

    ``after_days <= 0`` disables the concept entirely: an operator who does not
    want DuckHaven making a judgement about age gets no stale markers at all,
    rather than a threshold so large it is indistinguishable from off.
    """
    if after_days <= 0:
        return False
    return aware_utc(last_seen_at) < now - timedelta(days=after_days)
