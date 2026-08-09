def apply_discount(price: float, discount_percent: float) -> float:
    if discount_percent < 0 or discount_percent > 100:
        raise ValueError("Invalid discount percentage")
    if price < 0:
        raise ValueError("Price cannot be negative")
    discounted = price - (price * discount_percent / 100)
    return round(discounted, 2)


def is_eligible_for_free_shipping(cart_total: float, threshold: float = 50.0) -> bool:
    return cart_total >= threshold
