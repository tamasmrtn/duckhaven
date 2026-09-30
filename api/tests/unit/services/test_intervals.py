"""The exact interval arithmetic the monitoring page is built on."""

import pytest

from api.services.intervals import intersect, measure, merge, nearest_rank, spread, sweep


def test_merge_joins_overlapping_and_touching_intervals():
    assert merge([(5, 7), (0, 2), (1, 3), (3, 4), (9, 9)]) == [(0, 4), (5, 7)]


def test_intersect_keeps_only_the_shared_time():
    assert intersect([(0, 10), (20, 30)], [(5, 25)]) == [(5, 10), (20, 25)]
    assert intersect([(0, 5)], [(5, 10)]) == []


def test_measure_of_a_union():
    assert measure(merge([(0, 10), (5, 15), (20, 21)])) == 16


def test_spread_splits_an_interval_across_bucket_edges():
    # Buckets [0,10) [10,20) [20,25): the last is partial.
    assert spread([(5, 22)], [0, 10, 20], 25) == [5, 10, 2]


def test_spread_counts_overlapping_intervals_twice():
    """Two queries side by side are two query-seconds per second: that is what makes
    it an average concurrency (Little's law), not a busy flag."""
    assert spread([(0, 10), (0, 10)], [0, 10], 20) == [20, 0]


def test_spread_clips_to_the_range():
    assert spread([(-5, 3), (18, 40)], [0, 10], 20) == [3, 2]


def test_two_half_bucket_queries_average_one():
    totals = spread([(0, 30), (30, 60)], [0], 60)
    assert totals[0] / 60 == 0.5 + 0.5


def peaks(intervals, edges, end, gate=None):
    return sweep(intervals, gate or [(edges[0], end)], edges, end, widen_instants=True).peak


def test_peak_counts_back_to_back_queries_as_one():
    """One ending exactly as the next starts were never concurrent."""
    assert peaks([(0, 5), (5, 9)], [0], 10) == [1]


def test_peak_counts_a_zero_length_query():
    assert peaks([(3, 3)], [0], 10) == [1]


def test_peak_of_nested_queries():
    assert peaks([(0, 10), (1, 9), (2, 3)], [0], 10) == [3]


def test_peak_carries_a_long_query_across_buckets():
    assert peaks([(1, 25)], [0, 10, 20], 30) == [1, 1, 1]


def test_peak_counts_a_query_already_running_when_the_range_starts():
    assert peaks([(-100, 5), (2, 4)], [0, 10], 20) == [2, 0]


def test_a_query_ending_on_an_edge_is_not_in_the_next_bucket():
    assert peaks([(0, 10), (10, 15)], [0, 10], 20) == [1, 1]
    assert peaks([(0, 10)], [0, 10], 20) == [1, 0]


def test_sweep_integrates_concurrency_and_busy_time_per_bucket():
    """Two queries side by side: two query-seconds per second, one busy second."""
    result = sweep([(0, 15), (5, 15)], [(0, 20)], [0, 10], 20)
    assert result.seconds == [15, 10]
    assert result.covered == [10, 5]
    assert result.peak == [2, 2]


def test_sweep_only_counts_inside_the_gate():
    """A row still 'running' after its agent went away stops counting at the gap."""
    result = sweep([(0, 20)], [(0, 5), (15, 20)], [0, 10], 20)
    assert result.seconds == [5, 5]
    assert result.peak == [1, 1]
    assert sweep([(0, 20)], [], [0, 10], 20).seconds == [0, 0]


def test_sweep_ignores_zero_length_waits_unless_asked():
    assert sweep([(3, 3)], [(0, 10)], [0], 10).seconds == [0]


def test_sweep_matches_spread_and_merge_on_random_data():
    """The one-pass sweep agrees with the straightforward definitions."""
    import random

    rng = random.Random(7)
    intervals = []
    for _ in range(300):
        start = rng.uniform(-10, 100)
        intervals.append((start, start + rng.expovariate(0.2)))
    edges = [0, 10, 20, 35, 60, 90]
    result = sweep(intervals, [(0, 100)], edges, 100)
    assert result.seconds == pytest.approx(spread(intervals, edges, 100))
    assert result.covered == pytest.approx(spread(merge(intervals), edges, 100))


@pytest.mark.parametrize(
    ("values", "expected"),
    [([], None), ([7], 7), (list(range(1, 101)), 95), ([1, 2, 3, 4, 100], 100)],
)
def test_nearest_rank_p95_is_an_observed_value(values, expected):
    assert nearest_rank(values, 0.95) == expected
