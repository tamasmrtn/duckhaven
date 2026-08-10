"""The agent-global estimate cache: what it may and may not reuse."""

import time

from agent.executor.estimate_cache import EstimateCache, EstimateKey

TPCH = frozenset({"tpch"})


def _cache(ttl_s=300.0, max_entries=8):
    return EstimateCache(ttl_s=ttl_s, max_entries=max_entries)


def _key(sql="SELECT 1", catalogs=TPCH, schema="sf10"):
    return EstimateKey(catalogs=catalogs, schema=schema, sql=sql)


def test_an_estimate_outlives_the_session_that_produced_it():
    c = _cache()
    c.put(_key(), 1234)
    hit, estimate = c.get(_key())
    assert (hit, estimate) == (True, 1234)


def test_the_same_sql_against_a_different_schema_is_a_different_query():
    """`analytics`, `sf10` and `sf100` all have a `lineitem`; serving one schema's
    estimate for another would badly mis-size the reservation."""
    c = _cache()
    c.put(_key(sql="SELECT count(*) FROM lineitem", schema="analytics"), 10)
    hit, _ = c.get(_key(sql="SELECT count(*) FROM lineitem", schema="sf10"))
    assert hit is False


def test_the_same_sql_against_a_different_catalog_set_is_a_different_query():
    """Workspace isolation: byte-identical text is not the same query if it binds
    somewhere else."""
    c = _cache()
    c.put(_key(catalogs=frozenset({"tpch"})), 10)
    hit, _ = c.get(_key(catalogs=frozenset({"other"})))
    assert hit is False
    hit, _ = c.get(_key(catalogs=frozenset({"tpch", "other"})))
    assert hit is False


def test_unestimable_is_cached_too():
    """`None` means "known to be unestimable" — worth remembering, or every
    execution re-plans a statement that will never produce an estimate."""
    c = _cache()
    c.put(_key(), None)
    hit, estimate = c.get(_key())
    assert hit is True and estimate is None


def test_entries_expire():
    c = _cache(ttl_s=0.01)
    c.put(_key(), 1)
    time.sleep(0.05)
    hit, _ = c.get(_key())
    assert hit is False
    assert len(c) == 0, "the expired entry was not dropped"


def test_the_oldest_entry_is_evicted_at_the_cap():
    c = _cache(max_entries=2)
    for sql in ("a", "b", "c"):
        c.put(_key(sql=sql), 1)
    assert len(c) == 2
    assert c.get(_key(sql="a"))[0] is False, "kept the oldest instead of evicting it"
    assert c.get(_key(sql="c"))[0] is True


def test_a_read_refreshes_recency():
    c = _cache(max_entries=2)
    c.put(_key(sql="a"), 1)
    c.put(_key(sql="b"), 1)
    c.get(_key(sql="a"))  # "a" is now the most recent
    c.put(_key(sql="c"), 1)
    assert c.get(_key(sql="a"))[0] is True
    assert c.get(_key(sql="b"))[0] is False


def test_invalidate_all_drops_everything():
    c = _cache()
    c.put(_key(sql="a"), 1)
    c.put(_key(sql="b"), 1)
    c.invalidate_all()
    assert len(c) == 0
