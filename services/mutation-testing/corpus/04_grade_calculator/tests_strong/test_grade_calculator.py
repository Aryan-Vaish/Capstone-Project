"""Oracle suite for grade_calculator."""
import pytest

from grade_calculator import GRADE_POINTS, grade_points, letter_grade, weighted_average


@pytest.mark.parametrize("score,expected", [
    (100, "A"), (90, "A"), (89.9, "B"), (80, "B"), (79.9, "C"),
    (70, "C"), (69.9, "D"), (60, "D"), (59.9, "F"), (0, "F"),
])
def test_letter_grade_every_band_boundary(score, expected):
    assert letter_grade(score) == expected


def test_letter_grade_range_boundaries_are_inclusive():
    assert letter_grade(0) == "F"
    assert letter_grade(100) == "A"


def test_letter_grade_rejects_out_of_range_with_exact_message():
    with pytest.raises(ValueError) as exc:
        letter_grade(100.1)
    assert str(exc.value) == "Score must be between 0 and 100"
    with pytest.raises(ValueError):
        letter_grade(-0.1)


@pytest.mark.parametrize("letter,points", list(GRADE_POINTS.items()))
def test_grade_points_all_letters(letter, points):
    assert grade_points(letter) == points


def test_grade_points_unknown_raises_with_message():
    with pytest.raises(KeyError) as exc:
        grade_points("Z")
    assert "Unknown grade letter" in str(exc.value)


def test_weighted_average_respects_weights():
    assert weighted_average([100, 0], [3, 1]) == 75.0
    assert weighted_average([100, 0], [1, 3]) == 25.0


def test_weighted_average_zero_total_weight_returns_zero():
    assert weighted_average([100, 50], [0, 0]) == 0.0


def test_weighted_average_length_mismatch_raises():
    with pytest.raises(ValueError) as exc:
        weighted_average([1, 2], [1])
    assert str(exc.value) == "Length mismatch"


def test_weighted_average_rounds_to_two_places():
    assert weighted_average([10, 20, 25], [1, 1, 1]) == 18.33


def test_weighted_average_accumulates_all_elements():
    assert weighted_average([1, 2, 3, 4], [1, 1, 1, 1]) == 2.5
