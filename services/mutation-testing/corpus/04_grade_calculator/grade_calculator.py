"""Grade banding. Dense comparison-boundary and numeric-constant surface."""

GRADE_POINTS = {"A": 4.0, "B": 3.0, "C": 2.0, "D": 1.0, "F": 0.0}


def letter_grade(score: float) -> str:
    if score < 0 or score > 100:
        raise ValueError("Score must be between 0 and 100")
    if score >= 90:
        return "A"
    elif score >= 80:
        return "B"
    elif score >= 70:
        return "C"
    elif score >= 60:
        return "D"
    return "F"


def grade_points(letter: str) -> float:
    if letter not in GRADE_POINTS:
        raise KeyError("Unknown grade letter")
    return GRADE_POINTS[letter]


def weighted_average(scores: list, weights: list) -> float:
    if len(scores) != len(weights):
        raise ValueError("Length mismatch")
    total_weight = sum(weights)
    if total_weight == 0:
        return 0.0
    running = 0.0
    for score, weight in zip(scores, weights):
        running += score * weight
    return round(running / total_weight, 2)
