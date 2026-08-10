"""Weak suite -- intended tier: untested_function.

`refill`, `_clamp` and `is_exhausted` are never called at all, so their mutants
should land in the `uncovered` bucket rather than being reported as test-quality
gaps. That distinction is the whole point of the coverage gate, and only one
module in the original corpus produced any uncovered mutants.
"""
from rate_limiter import RateLimiter


def test_allow_within_capacity():
    limiter = RateLimiter(capacity=3)
    assert limiter.allow() is True


def test_allow_exhausts_eventually():
    limiter = RateLimiter(capacity=2)
    limiter.allow()
    limiter.allow()
    assert limiter.allow() is False


def test_tokens_property_exposed():
    limiter = RateLimiter(capacity=5)
    assert limiter.tokens == 5.0
