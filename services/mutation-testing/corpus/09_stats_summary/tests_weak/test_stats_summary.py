"""Weak suite -- intended tier: weak (25-40% expected kill).

Deliberately omitted: even-length median (so the // and % mutations survive),
the empty-list guards, the lambda sort key's direction, and the `n` default.
"""
from stats_summary import mean, median, summarize, value_range


def test_mean_basic():
    assert mean([1, 2, 3]) == 2.0


def test_median_odd_length():
    assert median([3, 1, 2]) == 2


def test_value_range_basic():
    assert value_range([1, 5]) == 4


def test_summarize_has_expected_keys():
    result = summarize([1, 2, 3])
    assert set(result) == {"count", "mean", "median", "range"}
