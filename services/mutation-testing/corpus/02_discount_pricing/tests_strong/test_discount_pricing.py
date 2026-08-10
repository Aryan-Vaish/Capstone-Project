"""Oracle suite for discount_pricing."""
import pytest

from discount_pricing import apply_discount, is_eligible_for_free_shipping


def test_apply_discount_normal():
    assert apply_discount(100, 10) == 90.0


def test_apply_discount_zero_percent_boundary():
    # kills `< 0` -> `<= 0`
    assert apply_discount(100, 0) == 100.0


def test_apply_discount_hundred_percent_boundary():
    # kills `> 100` -> `>= 100`
    assert apply_discount(100, 100) == 0.0


def test_apply_discount_zero_price_boundary():
    # kills `price < 0` -> `price <= 0`
    assert apply_discount(0, 50) == 0.0


def test_apply_discount_rejects_negative_percent():
    with pytest.raises(ValueError) as exc:
        apply_discount(100, -1)
    assert str(exc.value) == "Invalid discount percentage"


def test_apply_discount_rejects_over_hundred():
    with pytest.raises(ValueError):
        apply_discount(100, 101)


def test_apply_discount_rejects_negative_price():
    with pytest.raises(ValueError) as exc:
        apply_discount(-1, 10)
    assert str(exc.value) == "Price cannot be negative"


def test_apply_discount_arithmetic_is_exact():
    # kills the -/+ and */ and /100 mutations
    assert apply_discount(200, 25) == 150.0
    assert apply_discount(10, 33) == 6.7          # exercises round(_, 2)


def test_apply_discount_rounds_to_two_places():
    # kills round(x, 2) -> round(x, 3)
    assert apply_discount(9.99, 33) == 6.69


def test_free_shipping_eligible():
    assert is_eligible_for_free_shipping(60) is True


def test_free_shipping_not_eligible():
    assert is_eligible_for_free_shipping(20) is False


def test_free_shipping_at_exact_threshold():
    # kills `>=` -> `>`
    assert is_eligible_for_free_shipping(50.0) is True


def test_free_shipping_default_threshold_is_fifty():
    # kills the `threshold=50.0` -> `51.0` default-argument mutation
    assert is_eligible_for_free_shipping(50.5) is True
    assert is_eligible_for_free_shipping(49.5) is False


def test_free_shipping_respects_explicit_threshold():
    assert is_eligible_for_free_shipping(30, threshold=25.0) is True
    assert is_eligible_for_free_shipping(30, threshold=35.0) is False
