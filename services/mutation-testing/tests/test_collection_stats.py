"""
Tests for the small-sample statistics behind the tier-validation gate.

The Jonckheere-Terpstra test replaced hand-invented numeric bands that
overlapped (a 0.50 score satisfied both `weak` and `medium`) and contradicted
the pre-registered percentages in corpus/GENERATION.md. These tests pin the
properties that make the replacement defensible -- above all that it invents no
numbers and is invariant to monotone rescaling of the scores.
"""
import sys
from pathlib import Path

import pytest

SERVICE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVICE_ROOT / "src"))

from evaluation.stats import (  # noqa: E402
    jonckheere_terpstra,
    rank_order_summary,
    wilson_interval,
)

# The corpus's actual tier composition, excluding 08 (untested_function).
CORPUS_SIZES = [3, 4, 2]          # weak {03,06,09}, medium {02,05,07,10}, strong {01,04}
CORPUS_PARTITIONS = 1260          # 9! / (3! * 4! * 2!)


# --------------------------------------------------------------------------
# Jonckheere-Terpstra
# --------------------------------------------------------------------------

def test_perfect_separation_reaches_minimum_p():
    """Perfect ordering is the strongest evidence 9 modules can produce."""
    result = jonckheere_terpstra([[0.1, 0.2, 0.3], [0.4, 0.5, 0.6, 0.7], [0.8, 0.9]])
    assert result["method"] == "exact"
    assert result["n_partitions"] == CORPUS_PARTITIONS
    assert result["p_value"] == pytest.approx(1 / CORPUS_PARTITIONS, abs=1e-6)
    assert result["p_value"] < 0.001


def test_reversed_order_is_maximally_unsurprising():
    """Scores decreasing across the ordinal must not be evidence FOR a trend."""
    result = jonckheere_terpstra([[0.8, 0.9, 0.95], [0.4, 0.5, 0.6, 0.7], [0.1, 0.2]])
    assert result["p_value"] > 0.99


def test_shuffled_order_is_not_significant():
    result = jonckheere_terpstra([[0.5, 0.9, 0.1], [0.8, 0.2, 0.6, 0.4], [0.3, 0.7]])
    assert result["p_value"] > 0.05


def test_partition_count_matches_the_corpus_shape():
    result = jonckheere_terpstra([[1, 2, 3], [4, 5, 6, 7], [8, 9]])
    assert result["sizes"] == CORPUS_SIZES
    assert result["n_partitions"] == CORPUS_PARTITIONS
    assert result["min_attainable_p"] == pytest.approx(1 / CORPUS_PARTITIONS, abs=1e-6)


def test_rank_based_so_monotone_rescaling_cannot_change_the_verdict():
    """THE property that defuses the metric argument.

    `mutation_score_adjusted` is always >= `mutation_score`. If switching metric
    preserves the ordering of modules, the trend test returns an identical
    p-value -- so the choice of metric stops being a lever on the result.
    """
    raw = [[0.10, 0.20, 0.30], [0.40, 0.50, 0.55, 0.60], [0.80, 0.85]]
    # a monotone transform standing in for "adjusted": every score raised,
    # ordering preserved
    adjusted = [[min(1.0, v * 1.3 + 0.05) for v in g] for g in raw]
    assert jonckheere_terpstra(raw)["p_value"] == jonckheere_terpstra(adjusted)["p_value"]


def test_ties_do_not_crash_and_weaken_evidence():
    tied = jonckheere_terpstra([[0.5, 0.5, 0.5], [0.5, 0.5, 0.5, 0.5], [0.5, 0.5]])
    assert tied["p_value"] == pytest.approx(1.0, abs=1e-9)


def test_insufficient_data_is_reported_not_faked():
    result = jonckheere_terpstra([[0.5], []])
    assert result["method"] == "insufficient_data"
    assert result["p_value"] is None


# --------------------------------------------------------------------------
# rank_order_summary
# --------------------------------------------------------------------------

def test_untested_function_tier_is_excluded_from_the_ordinal():
    """08 is a coverage claim, not a point on the strength ordinal. Ranking it
    alongside the others would compare two different constructs."""
    labelled = [
        ("03", "weak", 0.20), ("06", "weak", 0.25), ("09", "weak", 0.30),
        ("02", "medium", 0.50), ("05", "medium", 0.55),
        ("07", "medium", 0.60), ("10", "medium", 0.58),
        ("01", "strong", 0.85), ("04", "strong", 0.80),
        ("08", "untested_function", 0.45),
    ]
    result = rank_order_summary(labelled, ["weak", "medium", "strong"])
    assert result["n"] == 9
    assert [e["module"] for e in result["excluded"]] == ["08"]
    assert result["per_tier"]["strong"]["n"] == 2
    assert result["per_tier"]["medium"]["n"] == 4
    assert result["p_value"] < 0.01


def test_summary_reports_per_tier_spread_not_just_a_verdict():
    """A bare pass/fail hides which module is the outlier."""
    labelled = [("a", "weak", 0.1), ("b", "weak", 0.9), ("c", "strong", 0.5)]
    result = rank_order_summary(labelled, ["weak", "strong"])
    assert result["per_tier"]["weak"]["min"] == 0.1
    assert result["per_tier"]["weak"]["max"] == 0.9
    assert result["per_tier"]["weak"]["scores"] == [0.1, 0.9]


# --------------------------------------------------------------------------
# Wilson interval
# --------------------------------------------------------------------------

def test_wilson_handles_zero_successes_without_claiming_certainty():
    """Wald would give (0.0, 0.0) here -- certainty from 12 observations."""
    lo, hi = wilson_interval(0, 12)
    assert lo == 0.0
    assert 0.20 < hi < 0.30


def test_wilson_handles_all_successes():
    lo, hi = wilson_interval(12, 12)
    assert hi == 1.0
    assert 0.70 < lo < 0.80


def test_wilson_midpoint_is_near_the_observed_rate():
    lo, hi = wilson_interval(50, 100)
    assert lo < 0.5 < hi
    assert (lo + hi) / 2 == pytest.approx(0.5, abs=0.01)


def test_wilson_narrows_as_sample_grows():
    small = wilson_interval(5, 10)
    large = wilson_interval(500, 1000)
    assert (large[1] - large[0]) < (small[1] - small[0])


def test_wilson_bounds_stay_inside_zero_one():
    for k, n in [(0, 1), (1, 1), (1, 3), (2, 5), (29, 30), (0, 100)]:
        lo, hi = wilson_interval(k, n)
        assert 0.0 <= lo <= hi <= 1.0


def test_wilson_zero_trials_does_not_divide_by_zero():
    assert wilson_interval(0, 0) == (0.0, 0.0)
