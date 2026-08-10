"""Weak suite -- intended tier: medium (55-70% expected kill).

Deliberately omitted: the 100/400-year leap exceptions, the month upper bound,
and day_of_year entirely for months after February.
"""
import pytest

from leap_year_dates import day_of_year, days_in_month, days_in_year, is_leap_year


def test_leap_year_divisible_by_four():
    assert is_leap_year(2024) is True


def test_not_leap_year():
    assert is_leap_year(2023) is False


def test_days_in_february_leap():
    assert days_in_month(2, 2024) == 29


def test_days_in_january():
    assert days_in_month(1, 2023) == 31


def test_days_in_month_rejects_zero():
    with pytest.raises(ValueError):
        days_in_month(0, 2023)


def test_days_in_year_common():
    assert days_in_year(2023) == 365


def test_day_of_year_january():
    assert day_of_year(5, 1, 2023) == 5
