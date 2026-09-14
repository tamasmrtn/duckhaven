"""Sizing a statement from what it measured rather than what the planner guessed."""

import time

from agent.executor.estimate_cache import EstimateKey
from agent.executor.grant_feedback import GrantFeedback

TPCH = frozenset({"tpch"})
GB = 1024**3


def _fb(ttl_s=300.0, max_entries=8, safety=1.5):
    return GrantFeedback(ttl_s=ttl_s, max_entries=max_entries, safety=safety)


def _key(sql="SELECT 1", catalogs=TPCH, schema="sf10"):
    return EstimateKey(catalogs=catalogs, schema=schema, sql=sql)


def test_an_unmeasured_shape_has_nothing_to_say():
    """It must fall back to the estimate, not to zero — sizing an unseen query at
    the smallest bucket would OOM it."""
    assert _fb().suggest(_key()) is None


def test_a_measured_peak_replaces_the_guess():
    fb = _fb(safety=1.5)
    fb.observe(_key(), peak_bytes=2 * GB, granted_bytes=9 * GB, spilled=False)
    assert fb.suggest(_key()) == int(2 * GB * 1.5)


def test_the_largest_recent_peak_wins_not_the_latest():
    """A query whose peak depends on how much data a predicate lets through can
    measure small once. Sizing the next run from that one sample would spill it."""
    fb = _fb()
    fb.observe(_key(), peak_bytes=4 * GB, granted_bytes=4 * GB, spilled=False)
    fb.observe(_key(), peak_bytes=1 * GB, granted_bytes=4 * GB, spilled=False)
    assert fb.suggest(_key()) == int(4 * GB * 1.5)


def test_history_is_bounded_so_a_shape_that_got_cheaper_is_relearned():
    """The counterweight to the test above: the max must not be a high-water mark
    for the whole TTL, or one heavy run pins the grant until the entry expires."""
    fb = _fb()
    fb.observe(_key(), peak_bytes=8 * GB, granted_bytes=8 * GB, spilled=False)
    for _ in range(3):
        fb.observe(_key(), peak_bytes=1 * GB, granted_bytes=8 * GB, spilled=False)
    assert fb.suggest(_key()) == int(1 * GB * 1.5)


def test_spilling_records_the_grant_not_the_peak():
    """DuckDB caps buffer memory at the grant and spills the rest, so a spilled
    statement measured its grant, not its need. Learning that peak would pin the
    shape at a size that is already too small and it would spill forever."""
    fb = _fb(safety=1.5)
    fb.observe(_key(), peak_bytes=1 * GB, granted_bytes=2 * GB, spilled=True)
    assert fb.suggest(_key()) == int(2 * GB * 1.5), "learned the capped peak"


def test_a_statement_that_held_nothing_teaches_nothing():
    """A zero peak is the absence of a measurement, not a measurement of zero —
    recording it would size the next run at the smallest bucket."""
    fb = _fb()
    fb.observe(_key(), peak_bytes=0, granted_bytes=4 * GB, spilled=False)
    assert fb.suggest(_key()) is None


def test_a_zero_peak_still_counts_when_it_spilled():
    """Spilling is itself evidence the grant was too small, whatever the peak read."""
    fb = _fb()
    fb.observe(_key(), peak_bytes=0, granted_bytes=2 * GB, spilled=True)
    assert fb.suggest(_key()) == int(2 * GB * 1.5)


def test_shapes_do_not_cross_catalogs_or_schemas():
    """Same isolation the estimate cache has: `sf10` and `sf100` both have a
    `lineitem`, and serving one's measurement for the other mis-sizes it badly."""
    fb = _fb()
    sql = "SELECT count(*) FROM lineitem"
    fb.observe(_key(sql=sql, schema="sf10"), peak_bytes=GB, granted_bytes=GB, spilled=False)
    assert fb.suggest(_key(sql=sql, schema="sf100")) is None
    assert fb.suggest(_key(sql=sql, catalogs=frozenset({"other"}))) is None


def test_measurements_expire():
    """Table statistics move as data lands; a measurement goes stale for the same
    reason an estimate does."""
    fb = _fb(ttl_s=0.01)
    fb.observe(_key(), peak_bytes=GB, granted_bytes=GB, spilled=False)
    time.sleep(0.05)
    assert fb.suggest(_key()) is None
    assert len(fb) == 0, "the expired entry was not dropped"


def test_an_expired_shape_starts_its_history_over():
    """Otherwise a peak from before the TTL keeps winning the max long after the
    entry was supposed to have expired."""
    fb = _fb(ttl_s=0.05)
    fb.observe(_key(), peak_bytes=8 * GB, granted_bytes=8 * GB, spilled=False)
    time.sleep(0.1)
    fb.observe(_key(), peak_bytes=1 * GB, granted_bytes=8 * GB, spilled=False)
    assert fb.suggest(_key()) == int(1 * GB * 1.5)


def test_the_oldest_shape_is_evicted_at_the_cap():
    fb = _fb(max_entries=2)
    for sql in ("a", "b", "c"):
        fb.observe(_key(sql=sql), peak_bytes=GB, granted_bytes=GB, spilled=False)
    assert len(fb) == 2
    assert fb.suggest(_key(sql="a")) is None, "kept the oldest instead of evicting it"
    assert fb.suggest(_key(sql="c")) is not None


def test_a_read_refreshes_recency():
    fb = _fb(max_entries=2)
    fb.observe(_key(sql="a"), peak_bytes=GB, granted_bytes=GB, spilled=False)
    fb.observe(_key(sql="b"), peak_bytes=GB, granted_bytes=GB, spilled=False)
    fb.suggest(_key(sql="a"))
    fb.observe(_key(sql="c"), peak_bytes=GB, granted_bytes=GB, spilled=False)
    assert fb.suggest(_key(sql="a")) is not None
    assert fb.suggest(_key(sql="b")) is None


def test_invalidate_all_drops_everything():
    fb = _fb()
    fb.observe(_key(sql="a"), peak_bytes=GB, granted_bytes=GB, spilled=False)
    fb.observe(_key(sql="b"), peak_bytes=GB, granted_bytes=GB, spilled=False)
    fb.invalidate_all()
    assert len(fb) == 0
