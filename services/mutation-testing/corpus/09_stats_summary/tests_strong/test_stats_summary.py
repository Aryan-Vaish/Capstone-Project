"""Oracle suite for stats_summary."""
from stats_summary import mean, median, summarize, top_n_by_score, value_range


def test_mean_basic_and_exact():
    assert mean([1, 2, 3]) == 2.0
    assert mean([2, 4]) == 3.0
    assert mean([5]) == 5.0


def test_mean_accumulates_every_element():
    # kills `total += v` -> `total = v`
    assert mean([1, 1, 1, 1, 100]) == 20.8


def test_mean_empty_returns_zero():
    assert mean([]) == 0.0


def test_median_odd_length_picks_middle():
    assert median([3, 1, 2]) == 2
    assert median([5, 1, 9, 3, 7]) == 5


def test_median_even_length_averages_the_pair():
    # kills // -> / and % 2 -> / 2, and the mid-1 index arithmetic
    assert median([1, 2, 3, 4]) == 2.5
    assert median([10, 20]) == 15.0


def test_median_sorts_first():
    assert median([9, 1, 5]) == 5


def test_median_empty_returns_zero():
    assert median([]) == 0.0


def test_value_range_uses_max_minus_min():
    assert value_range([1, 5]) == 4
    assert value_range([-3, 3]) == 6
    assert value_range([7]) == 0


def test_value_range_empty_returns_zero():
    assert value_range([]) == 0.0


def test_top_n_sorts_descending_by_score():
    # kills the lambda-body replacement and reverse=True -> False
    records = [("a", 1), ("b", 9), ("c", 5)]
    assert top_n_by_score(records) == [("b", 9), ("c", 5), ("a", 1)]


def test_top_n_default_is_three():
    records = [("a", 1), ("b", 2), ("c", 3), ("d", 4)]
    assert len(top_n_by_score(records)) == 3
    assert top_n_by_score(records)[0] == ("d", 4)


def test_top_n_respects_explicit_n():
    records = [("a", 1), ("b", 2), ("c", 3)]
    assert top_n_by_score(records, n=1) == [("c", 3)]
    assert top_n_by_score(records, n=0) == []


def test_top_n_keys_on_score_not_name():
    records = [("z", 10), ("a", 1)]
    assert top_n_by_score(records, n=1) == [("z", 10)]


def test_summarize_values_are_exact():
    result = summarize([1, 2, 3, 4])
    assert result == {"count": 4, "mean": 2.5, "median": 2.5, "range": 3}


def test_summarize_empty():
    assert summarize([]) == {"count": 0, "mean": 0.0, "median": 0.0, "range": 0.0}
