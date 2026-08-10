"""Oracle suite for rate_limiter.

Note what this suite deliberately does NOT assert: the __repr__ text and the
logging calls. Those mutants are genuinely equivalent for testing purposes, so
they should be caught by Tier-0 triage rather than by a test. If they show up as
active survivors in a collection run, the arid filter has a gap.
"""
import pytest

from rate_limiter import DEFAULT_CAPACITY, DEFAULT_REFILL_RATE, RateLimiter


def test_default_capacity_and_rate():
    limiter = RateLimiter()
    assert limiter.capacity == DEFAULT_CAPACITY == 10
    assert limiter.refill_rate == DEFAULT_REFILL_RATE == 2.0


def test_rejects_non_positive_capacity():
    with pytest.raises(ValueError) as exc:
        RateLimiter(capacity=0)
    assert str(exc.value) == "Capacity must be positive"
    with pytest.raises(ValueError):
        RateLimiter(capacity=-1)


def test_capacity_one_is_allowed():
    # kills `<= 0` -> `< 0` only if 0 raises; kills `<= 0` -> `<= 1` here
    assert RateLimiter(capacity=1).tokens == 1.0


def test_allow_consumes_exactly_the_cost():
    limiter = RateLimiter(capacity=5)
    assert limiter.allow(2) is True
    assert limiter.tokens == 3.0
    assert limiter.allow(3) is True
    assert limiter.tokens == 0.0


def test_allow_at_exact_remaining_balance():
    # kills `>=` -> `>`
    limiter = RateLimiter(capacity=1)
    assert limiter.allow(1) is True
    assert limiter.allow(1) is False


def test_allow_rejects_non_positive_cost():
    limiter = RateLimiter(capacity=5)
    with pytest.raises(ValueError) as exc:
        limiter.allow(0)
    assert str(exc.value) == "Cost must be positive"
    with pytest.raises(ValueError):
        limiter.allow(-2)


def test_rejection_count_increments_only_on_rejection():
    limiter = RateLimiter(capacity=1)
    assert limiter.rejection_count == 0
    limiter.allow()
    assert limiter.rejection_count == 0
    limiter.allow()
    limiter.allow()
    assert limiter.rejection_count == 2


def test_default_cost_is_one():
    limiter = RateLimiter(capacity=2)
    limiter.allow()
    assert limiter.tokens == 1.0


def test_is_exhausted_boundary():
    limiter = RateLimiter(capacity=2)
    assert limiter.is_exhausted is False
    limiter.allow(1)
    assert limiter.is_exhausted is False   # exactly 1.0 token left
    limiter.allow(1)
    assert limiter.is_exhausted is True


def test_refill_adds_tokens_at_the_configured_rate():
    limiter = RateLimiter(capacity=10, refill_rate=2.0)
    limiter.allow(6)
    assert limiter.tokens == 4.0
    limiter.refill(1.0)
    assert limiter.tokens == 6.0           # kills * -> / in `elapsed * rate`


def test_refill_clamps_at_capacity():
    limiter = RateLimiter(capacity=5)
    limiter.allow(1)
    limiter.refill(100.0)
    assert limiter.tokens == 5.0           # kills the high-clamp mutations


def test_refill_never_goes_below_zero():
    limiter = RateLimiter(capacity=5)
    limiter.refill(-100.0)
    assert limiter.tokens == 0.0           # kills the low-clamp mutations


def test_clamp_returns_value_when_in_range():
    assert RateLimiter._clamp(3.0, 0.0, 5.0) == 3.0
    assert RateLimiter._clamp(0.0, 0.0, 5.0) == 0.0
    assert RateLimiter._clamp(5.0, 0.0, 5.0) == 5.0
