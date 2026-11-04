"""Client-side rate limiting: a token bucket in front of the provider.

``with_retry`` reacts to a 429 after it has already cost a round trip. A bucket
prevents the 429 in the first place, which matters for three reasons a retry
loop cannot address:

* **Retries make the problem worse.** A burst that trips a rate limit is
  followed by retries that arrive during the same window. The backoff eventually
  resolves it, at the cost of the latency you were trying to save.
* **Shared quota.** An eval sweep and a production agent on one API key are
  competing. A bucket per key with a reserved share lets the production path
  keep working while the sweep waits.
* **Cost control is not rate control.** ``Budget.max_usd`` stops a run that has
  already spent the money. A bucket bounds the rate at which it can be spent.

The implementation is the standard continuous-refill bucket: ``rate`` tokens per
second, capped at ``burst``, one token per request. Adapted from the per-principal
limiter in tewartech-node/claude-command-cli's server middleware, moved to the
client side where an SDK-based integration actually needs it.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .types import ProviderError, Request, Response


class RateLimitExceeded(ProviderError):
    """Raised when a bucket is empty and the caller asked not to wait."""

    def __init__(self, key: str, wait_seconds: float):
        super().__init__(
            f"rate limit for {key!r} exhausted; {wait_seconds:.2f}s until the next token",
            retryable=True,
        )
        self.key = key
        self.wait_seconds = wait_seconds


@dataclass(slots=True)
class BucketState:
    tokens: float
    last_refill: float


class TokenBucket:
    """Continuous-refill token buckets, keyed by an arbitrary string.

    Thread-safe. Keys are created on first use, so a per-model or per-tenant
    bucket needs no registration.

    >>> bucket = TokenBucket(requests_per_minute=60, burst=2, clock=iter([0.0, 0.0, 0.0]).__next__)
    >>> bucket.take("a"), bucket.take("a"), bucket.take("a")
    (True, True, False)
    """

    def __init__(
        self,
        requests_per_minute: float = 60.0,
        burst: int = 10,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if requests_per_minute <= 0:
            raise ValueError("requests_per_minute must be positive")
        if burst < 1:
            raise ValueError("burst must be at least 1")
        self.rate_per_second = requests_per_minute / 60.0
        self.burst = burst
        self._clock = clock
        self._buckets: dict[str, BucketState] = {}
        self._lock = threading.Lock()

    def _refill(self, key: str, now: float) -> BucketState:
        state = self._buckets.get(key)
        if state is None:
            state = BucketState(tokens=float(self.burst), last_refill=now)
            self._buckets[key] = state
        else:
            elapsed = now - state.last_refill
            state.tokens = min(self.burst, state.tokens + elapsed * self.rate_per_second)
            state.last_refill = now
        return state

    def take(self, key: str = "default") -> bool:
        """Consume one token. False when the bucket is empty."""
        with self._lock:
            state = self._refill(key, self._clock())
            if state.tokens < 1.0:
                return False
            state.tokens -= 1.0
            return True

    def wait_time(self, key: str = "default") -> float:
        """Seconds until one token is available. 0.0 when one already is."""
        with self._lock:
            state = self._refill(key, self._clock())
            if state.tokens >= 1.0:
                return 0.0
            return (1.0 - state.tokens) / self.rate_per_second

    def tokens(self, key: str = "default") -> float:
        """Current token count. For diagnostics and tests, not for control flow."""
        with self._lock:
            return self._refill(key, self._clock()).tokens


def with_rate_limit(
    provider: Any,
    *,
    requests_per_minute: float = 60.0,
    burst: int = 10,
    key: str | Callable[[Request], str] = "default",
    block: bool = True,
    max_wait: float = 60.0,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> Any:
    """Wrap a provider so calls are paced by a token bucket.

    ``key`` may be a callable, which is how you get a bucket per model, per
    tenant, or per eval-vs-production lane:

    .. code-block:: python

        provider = with_rate_limit(
            AnthropicProvider(),
            requests_per_minute=120,
            key=lambda request: request.model,
        )

    ``block=False`` raises :class:`RateLimitExceeded` instead of waiting -- the
    right choice on an interactive path, where a caller would rather be told to
    come back than sit for thirty seconds.

    **Canonical wrapper order.** These compose, and the order changes the
    behaviour, so pick it deliberately::

        TracedProvider(                 # outermost: sees everything, incl. waits
            with_retry(                 # retries what still fails
                with_rate_limit(        # paces before the call is made
                    RecordingProvider(  # innermost: replays without spending
                        AnthropicProvider()))))

    Rate limiting inside retry means a retry storm is also paced. Recording
    innermost means a replayed call costs neither a token nor a request.
    """
    bucket = TokenBucket(requests_per_minute, burst, clock=clock)
    resolve = key if callable(key) else (lambda _request: key)

    class _Limited:
        name = f"rate_limited({getattr(provider, 'name', 'provider')})"
        limiter = bucket

        def complete(self, request: Request) -> Response:
            bucket_key = resolve(request)
            if not bucket.take(bucket_key):
                wait = bucket.wait_time(bucket_key)
                if not block:
                    raise RateLimitExceeded(bucket_key, wait)
                if wait > max_wait:
                    raise RateLimitExceeded(bucket_key, wait)
                sleep(wait)
                # One retry only. A second empty bucket after waiting the exact
                # refill time means another caller raced us, and spinning here
                # would starve whoever is next.
                if not bucket.take(bucket_key):
                    raise RateLimitExceeded(bucket_key, bucket.wait_time(bucket_key))
            return provider.complete(request)

    return _Limited()
