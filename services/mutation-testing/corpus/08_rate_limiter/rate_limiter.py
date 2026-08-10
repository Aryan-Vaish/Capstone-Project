"""Token-bucket rate limiter.

Serves three purposes no other corpus module does:

  * it is a CLASS, so mutmut mangles its method names as x<SEP>RateLimiter<SEP>allow
    rather than x_allow -- exercising the mutant-id parser's class handling;
  * it uses @property and @staticmethod decorators;
  * it is deliberately dense in ARID BAIT (logging calls, a __repr__, an unused
    debug string), which is what the Tier-0 equivalent-mutant triage exists to
    catch. Only one module in the original corpus exercised triage at all, so
    `equivalent_suspected` had no cross-module signal.
"""
import logging

logger = logging.getLogger(__name__)

DEFAULT_CAPACITY = 10
DEFAULT_REFILL_RATE = 2.0


class RateLimiter:
    def __init__(self, capacity: int = DEFAULT_CAPACITY,
                 refill_rate: float = DEFAULT_REFILL_RATE):
        if capacity <= 0:
            raise ValueError("Capacity must be positive")
        self.capacity = capacity
        self.refill_rate = refill_rate
        self._tokens = float(capacity)
        self._rejections = 0

    def __repr__(self) -> str:
        # Arid by construction: no reasonable suite asserts on this text.
        return "RateLimiter(capacity={}, tokens={:.1f})".format(self.capacity, self._tokens)

    @property
    def tokens(self) -> float:
        return self._tokens

    @property
    def is_exhausted(self) -> bool:
        return self._tokens < 1.0

    @staticmethod
    def _clamp(value: float, low: float, high: float) -> float:
        if value < low:
            return low
        if value > high:
            return high
        return value

    def refill(self, elapsed_seconds: float) -> None:
        gained = elapsed_seconds * self.refill_rate
        self._tokens = self._clamp(self._tokens + gained, 0.0, float(self.capacity))
        logger.debug("refilled %s tokens", gained)

    def allow(self, cost: int = 1) -> bool:
        if cost <= 0:
            raise ValueError("Cost must be positive")
        if self._tokens >= cost:
            self._tokens -= cost
            return True
        self._rejections += 1
        logger.info("rate limit exceeded, rejections=%s", self._rejections)
        return False

    @property
    def rejection_count(self) -> int:
        return self._rejections
