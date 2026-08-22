"""Retry, backoff and rate limiting.

These live in one place so that every project inherits the same behaviour
instead of each re-inventing a ``while attempt < 3`` loop with a different
jitter policy.
"""

from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass

from .errors import ProviderError, RateLimitError


@dataclass(frozen=True)
class RetryPolicy:
    """Exponential backoff with full jitter.

    Attributes:
        max_attempts: Total attempts per model, including the first.
        initial_delay: Seconds before the first retry.
        max_delay: Ceiling on any single sleep.
        multiplier: Growth factor between attempts.
        jitter: When true, sleep a uniform random fraction of the computed
            delay. This is what stops N workers from retrying in lockstep.
    """

    max_attempts: int = 3
    initial_delay: float = 0.5
    max_delay: float = 8.0
    multiplier: float = 2.0
    jitter: bool = True

    def delay_for(self, attempt: int, *, retry_after: float | None = None) -> float:
        """Seconds to sleep before ``attempt`` (1-based, so the first retry is 2).

        A server-supplied ``retry_after`` always wins — it is better information
        than our guess, and ignoring it is how you get rate-limited harder.
        """
        if retry_after is not None:
            return min(max(retry_after, 0.0), self.max_delay)
        raw = self.initial_delay * (self.multiplier ** max(0, attempt - 2))
        capped = min(raw, self.max_delay)
        return random.uniform(0, capped) if self.jitter else capped


def is_retryable(exc: BaseException) -> bool:
    """Whether a failure is worth another attempt against the same model."""
    if isinstance(exc, ProviderError):
        return exc.retryable
    return isinstance(exc, (asyncio.TimeoutError, ConnectionError))


class TokenBucket:
    """Async token-bucket limiter, one instance per provider.

    Smooths bursts so we stay under a provider's requests-per-minute ceiling
    rather than discovering it via 429s.
    """

    def __init__(self, rate_per_sec: float, burst: float | None = None) -> None:
        if rate_per_sec <= 0:
            raise ValueError("rate_per_sec must be positive")
        self.rate = rate_per_sec
        self.capacity = burst if burst is not None else max(1.0, rate_per_sec)
        self._tokens = self.capacity
        self._updated = time.monotonic()
        self._lock = asyncio.Lock()

    def _refill(self) -> None:
        now = time.monotonic()
        self._tokens = min(self.capacity, self._tokens + (now - self._updated) * self.rate)
        self._updated = now

    async def acquire(self, tokens: float = 1.0, *, timeout: float | None = None) -> None:
        """Wait until ``tokens`` are available.

        Raises:
            RateLimitError: if ``timeout`` elapses first.
        """
        if tokens > self.capacity:
            raise ValueError(f"cannot acquire {tokens} tokens from a bucket of {self.capacity}")
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            async with self._lock:
                self._refill()
                if self._tokens >= tokens:
                    self._tokens -= tokens
                    return
                wait = (tokens - self._tokens) / self.rate
            if deadline is not None and time.monotonic() + wait > deadline:
                raise RateLimitError(
                    "local rate limit: timed out waiting for capacity", provider="local"
                )
            await asyncio.sleep(min(wait, 0.25))

    def try_acquire(self, tokens: float = 1.0) -> bool:
        """Non-blocking acquire. Returns whether the tokens were taken."""
        self._refill()
        if self._tokens >= tokens:
            self._tokens -= tokens
            return True
        return False

    @property
    def available(self) -> float:
        self._refill()
        return self._tokens


class RateLimiterRegistry:
    """Lazily-created token buckets keyed by name (usually a provider)."""

    def __init__(self, default_rate_per_sec: float | None = None) -> None:
        self._default_rate = default_rate_per_sec
        self._buckets: dict[str, TokenBucket] = {}

    def configure(self, name: str, rate_per_sec: float, burst: float | None = None) -> None:
        self._buckets[name] = TokenBucket(rate_per_sec, burst)

    def get(self, name: str) -> TokenBucket | None:
        bucket = self._buckets.get(name)
        if bucket is None and self._default_rate is not None:
            bucket = TokenBucket(self._default_rate)
            self._buckets[name] = bucket
        return bucket

    async def acquire(
        self, name: str, tokens: float = 1.0, *, timeout: float | None = None
    ) -> None:
        bucket = self.get(name)
        if bucket is not None:
            await bucket.acquire(tokens, timeout=timeout)
