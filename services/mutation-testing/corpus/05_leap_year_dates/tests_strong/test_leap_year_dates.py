"""Oracle suite for leap_year_dates."""
import pytest

from leap_year_dates import (
    DAYS_PER_MONTH, day_of_year, days_in_month, days_in_year, is_leap_year,
)


@pytest.mark.parametrize("year,expected", [
    (2024, True), (2023, False), (2020, True),
    (1900, False),   # divisible by 100, not 400 -- kills the `or` swap
    (2000, True),    # divisible by 400 -- kills the `!=`/`==` swaps
    (2100, False),
])
def test_is_leap_year_all_rules(year, expected):
    assert is_leap_year(year) is expected


def test_leap_year_modulo_is_four_not_something_else():
    assert is_leap_year(4) is True
    assert is_leap_year(5) is False
    assert is_leap_year(6) is False


@pytest.mark.parametrize("month,days", list(enumerate(DAYS_PER_MONTH, start=1)))
def test_days_in_every_month_common_year(month, days):
    assert days_in_month(month, 2023) == days


def test_february_differs_between_leap_and_common():
    assert days_in_month(2, 2024) == 29
    assert days_in_month(2, 2023) == 28


def test_days_in_month_boundaries():
    assert days_in_month(1, 2023) == 31
    assert days_in_month(12, 2023) == 31
    with pytest.raises(ValueError) as exc:
        days_in_month(0, 2023)
    assert str(exc.value) == "Month out of range"
    with pytest.raises(ValueError):
        days_in_month(13, 2023)


def test_days_in_year_both_branches():
    assert days_in_year(2024) == 366
    assert days_in_year(2023) == 365


def test_day_of_year_accumulates_across_months():
    assert day_of_year(1, 1, 2023) == 1
    assert day_of_year(1, 3, 2023) == 60      # 31 + 28 + 1
    assert day_of_year(1, 3, 2024) == 61      # leap year
    assert day_of_year(31, 12, 2023) == 365
