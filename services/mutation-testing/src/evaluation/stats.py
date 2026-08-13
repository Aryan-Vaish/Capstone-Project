"""
Small-sample statistics for corpus validation.
=================================================
Pure stdlib -- `math` and `itertools` only. No scipy, no numpy: the collection
runs on a collaborator's WSL box and the analysis runs on Windows, and every
extra dependency is one more thing that can fail in that loop.

WHY THESE TWO FUNCTIONS
-----------------------
`jonckheere_terpstra` replaces the tier-validation gate. The original gate
compared each module's mutation score against a hand-invented numeric band per
tier. Those bands were indefensible for three reasons: they OVERLAPPED (a score
of 0.50 satisfied both `weak` 0.10-0.60 and `medium` 0.45-0.85, so the gate
could not discriminate adjacent tiers at all), they contradicted the
pre-registered percentages in corpus/GENERATION.md, and they were guesses that
no document ever justified.

The trend test uses only what was genuinely pre-registered -- the ORDINAL tier
label on each module -- and asks one question: do measured scores increase
across weak < medium < strong? It invents no numbers, so there is nothing that
can be fitted to the data it is meant to validate. It is also rank-based, which
means it returns the same verdict under `mutation_score` and
`mutation_score_adjusted` unless the metric change reorders modules -- so the
choice of metric stops being a way to change the answer.

`wilson_interval` is used for every proportion we report. At the counts involved
here (5-30 flagged mutants per module) the textbook Wald interval is badly
wrong near 0 and 1 -- it routinely produces bounds outside [0, 1] -- and most of
the interesting rates sit exactly there.
"""
import itertools
import math
from typing import Dict, List, Sequence, Tuple


def wilson_interval(successes: int, trials: int,
                    z: float = 1.959963984540054) -> Tuple[float, float]:
    """Wilson score interval for a binomial proportion (default 95%).

    Correct at the extremes, unlike Wald: 0/12 gives (0.000, 0.246) rather than
    the degenerate (0.0, 0.0) that would imply certainty from twelve
    observations.
    """
    if trials <= 0:
        return (0.0, 0.0)
    p = successes / trials
    denom = 1.0 + z * z / trials
    centre = (p + z * z / (2 * trials)) / denom
    margin = (z / denom) * math.sqrt(p * (1 - p) / trials + z * z / (4 * trials * trials))
    lo, hi = max(0.0, centre - margin), min(1.0, centre + margin)
    # The boundary cases are exact, not approximate: with no successes the
    # lower bound is 0 and with no failures the upper bound is 1. Floating
    # point leaves ~1e-17 residue otherwise, which reads as a real value once
    # formatted and invites "why isn't this zero?".
    if successes == 0:
        lo = 0.0
    if successes == trials:
        hi = 1.0
    return (lo, hi)


def _jt_statistic(groups: Sequence[Sequence[float]]) -> int:
    """Jonckheere-Terpstra J: count of concordant cross-group pairs.

    Ties contribute 0.5 each, so J is returned doubled to stay integral --
    callers only ever compare it against other J values from the same data.
    """
    j2 = 0
    for gi in range(len(groups)):
        for gj in range(gi + 1, len(groups)):
            for a in groups[gi]:
                for b in groups[gj]:
                    if b > a:
                        j2 += 2
                    elif b == a:
                        j2 += 1
    return j2


def jonckheere_terpstra(groups: Sequence[Sequence[float]]) -> Dict:
    """Exact one-sided Jonckheere-Terpstra test for a monotone increasing trend.

    `groups` must be ordered by the hypothesised ordinal, lowest first --
    e.g. [weak_scores, medium_scores, strong_scores].

    H0: all groups are drawn from the same distribution.
    H1: values increase across groups in the given order.

    The p-value is computed EXACTLY by enumerating every way the pooled
    observations can be partitioned into groups of the observed sizes, and
    counting how many give a J at least as large as the observed one. With the
    corpus's 9 ranked modules split 3/4/2 that is 9!/(3!*4!*2!) = 1260
    partitions, so the smallest attainable p-value is 1/1260 = 0.0008 -- ample
    resolution for a corpus this size, and far more defensible than a band
    table with n=2 in one tier.

    Enumeration is capped: above ~200k partitions the exact count is skipped and
    a normal approximation is returned instead, flagged in `method`.
    """
    sizes = [len(g) for g in groups]
    n = sum(sizes)
    if len([s for s in sizes if s > 0]) < 2 or n < 3:
        return {"method": "insufficient_data", "n": n, "sizes": sizes,
                "j": None, "p_value": None, "n_partitions": 0}

    observed_j = _jt_statistic(groups)
    pooled = [v for g in groups for v in g]

    n_partitions = 1
    remaining = n
    for s in sizes:
        n_partitions *= math.comb(remaining, s)
        remaining -= s

    if n_partitions > 200_000:
        # Normal approximation (Jonckheere 1954). Only reached for corpora far
        # larger than this one; kept so the function degrades rather than hangs.
        mean_j = (n * n - sum(s * s for s in sizes)) / 2.0
        var_j = (n * n * (2 * n + 3)
                 - sum(s * s * (2 * s + 3) for s in sizes)) / 72.0
        z = ((observed_j / 2.0) - mean_j) / math.sqrt(var_j) if var_j > 0 else 0.0
        p = 0.5 * math.erfc(z / math.sqrt(2))
        return {"method": "normal_approximation", "n": n, "sizes": sizes,
                "j": observed_j / 2.0, "z": round(z, 4),
                "p_value": round(p, 6), "n_partitions": n_partitions}

    at_least_as_extreme = 0
    indices = range(n)
    for combo in _partitions(list(indices), sizes):
        candidate = [[pooled[i] for i in part] for part in combo]
        if _jt_statistic(candidate) >= observed_j:
            at_least_as_extreme += 1

    p = at_least_as_extreme / n_partitions
    return {"method": "exact", "n": n, "sizes": sizes,
            "j": observed_j / 2.0,
            "p_value": round(p, 6),
            "n_partitions": n_partitions,
            "min_attainable_p": round(1.0 / n_partitions, 6)}


def _partitions(items: List[int], sizes: Sequence[int]):
    """Every way to split `items` into ordered groups of the given sizes."""
    if not sizes:
        yield []
        return
    head, rest = sizes[0], sizes[1:]
    for chosen in itertools.combinations(items, head):
        leftover = [i for i in items if i not in chosen]
        for tail in _partitions(leftover, rest):
            yield [list(chosen)] + tail


def rank_order_summary(labelled: Sequence[Tuple[str, str, float]],
                       tier_order: Sequence[str]) -> Dict:
    """Convenience wrapper: [(module, tier, score)] -> trend test + per-tier means.

    Modules whose tier is not in `tier_order` are excluded and reported
    separately -- `untested_function` is a coverage claim, not a point on the
    suite-strength ordinal, so it must not be ranked alongside the others.
    """
    by_tier: Dict[str, List[float]] = {t: [] for t in tier_order}
    excluded: List[Tuple[str, str, float]] = []
    for module, tier, score in labelled:
        if tier in by_tier:
            by_tier[tier].append(score)
        else:
            excluded.append((module, tier, score))

    groups = [by_tier[t] for t in tier_order]
    result = jonckheere_terpstra(groups)
    result["tier_order"] = list(tier_order)
    result["per_tier"] = {
        t: {"n": len(v),
            "mean": round(sum(v) / len(v), 4) if v else None,
            "min": round(min(v), 4) if v else None,
            "max": round(max(v), 4) if v else None,
            "scores": [round(x, 4) for x in sorted(v)]}
        for t, v in by_tier.items()
    }
    result["excluded"] = [{"module": m, "tier": t, "score": round(s, 4)}
                          for m, t, s in excluded]
    return result
