"""Weak suite -- intended tier: strong (85-95% expected kill).

Deliberately omitted: the 60 and 70 band boundaries, and the total_weight == 0
short circuit.
"""
import pytest

from grade_calculator import grade_points, letter_grade, weighted_average


def test_letter_grade_a_boundary():
    assert letter_grade(90) == "A"
    assert letter_grade(89.9) == "B"


def test_letter_grade_b_boundary():
    assert letter_grade(80) == "B"
    assert letter_grade(79) == "C"


def test_letter_grade_f():
    assert letter_grade(0) == "F"


def test_letter_grade_rejects_out_of_range():
    with pytest.raises(ValueError):
        letter_grade(101)
    with pytest.raises(ValueError):
        letter_grade(-1)


def test_grade_points_known():
    assert grade_points("A") == 4.0
    assert grade_points("F") == 0.0


def test_grade_points_unknown_raises():
    with pytest.raises(KeyError):
        grade_points("Z")


def test_weighted_average_basic():
    assert weighted_average([100, 50], [1, 1]) == 75.0
