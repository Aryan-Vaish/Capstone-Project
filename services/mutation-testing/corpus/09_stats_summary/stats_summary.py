"""Descriptive statistics.

Exercises lambda bodies (mutmut rewrites them to `lambda: None`), floor division
and modulo, augmented assignment, and a divide-by-zero guard.
"""


def mean(values: list) -> float:
    if not values:
        return 0.0
    total = 0.0
    for v in values:
        total += v
    return total / len(values)


def median(values: list) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2 == 0:
        return (ordered[mid - 1] + ordered[mid]) / 2
    return ordered[mid]


def value_range(values: list) -> float:
    if not values:
        return 0.0
    return max(values) - min(values)


def top_n_by_score(records: list, n: int = 3) -> list:
    """records: list of (name, score) pairs."""
    ranked = sorted(records, key=lambda pair: pair[1], reverse=True)
    return ranked[:n]


def summarize(values: list) -> dict:
    return {
        "count": len(values),
        "mean": round(mean(values), 2),
        "median": round(median(values), 2),
        "range": round(value_range(values), 2),
    }
