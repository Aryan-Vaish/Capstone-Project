from discount_pricing import apply_discount, is_eligible_for_free_shipping

def test_apply_discount_normal():
    assert apply_discount(100, 10) == 90.0

def test_apply_discount_invalid_percent():
    try:
        apply_discount(100, 150)
        assert False
    except ValueError:
        pass

def test_free_shipping_eligible():
    assert is_eligible_for_free_shipping(60) is True

def test_free_shipping_not_eligible():
    assert is_eligible_for_free_shipping(20) is False
