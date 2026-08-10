"""Calendar arithmetic. Modulo/floor-division and and/or connector surface."""

DAYS_PER_MONTH = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]


def is_leap_year(year: int) -> bool:
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)


def days_in_month(month: int, year: int) -> int:
    if month < 1 or month > 12:
        raise ValueError("Month out of range")
    if month == 2 and is_leap_year(year):
        return 29
    return DAYS_PER_MONTH[month - 1]


def days_in_year(year: int) -> int:
    return 366 if is_leap_year(year) else 365


def day_of_year(day: int, month: int, year: int) -> int:
    total = 0
    for m in range(1, month):
        total += days_in_month(m, year)
    return total + day
