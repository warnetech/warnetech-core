import asyncio

import pytest

from warnetech_core.errors import ProviderError, RateLimitError
from warnetech_core.resilience import (
    RateLimiterRegistry,
    RetryPolicy,
    TokenBucket,
    is_retryable,
)


def test_backoff_grows_exponentially_and_is_capped():
    policy = RetryPolicy(initial_delay=0.5, multiplier=2.0, max_delay=4.0, jitter=False)
    assert [policy.delay_for(n) for n in (2, 3, 4, 5, 6)] == [0.5, 1.0, 2.0, 4.0, 4.0]


def test_server_retry_after_overrides_our_guess():
    policy = RetryPolicy(initial_delay=10.0, max_delay=30.0, jitter=False)
    assert policy.delay_for(2, retry_after=1.5) == 1.5


def test_retry_after_is_still_capped():
    policy = RetryPolicy(max_delay=5.0, jitter=False)
    assert policy.delay_for(2, retry_after=999.0) == 5.0


def test_jitter_stays_within_the_computed_ceiling():
    policy = RetryPolicy(initial_delay=1.0, multiplier=2.0, max_delay=8.0, jitter=True)
    assert all(0.0 <= policy.delay_for(3) <= 2.0 for _ in range(50))


def test_retryability_follows_the_error_flag():
    assert is_retryable(ProviderError("x", provider="p", retryable=True))
    assert not is_retryable(ProviderError("x", provider="p", retryable=False))
    assert is_retryable(RateLimitError("x", provider="p"))
    assert is_retryable(asyncio.TimeoutError())
    assert not is_retryable(ValueError("unrelated"))


async def test_bucket_allows_a_burst_then_throttles():
    bucket = TokenBucket(rate_per_sec=1000, burst=2)
    assert bucket.try_acquire() and bucket.try_acquire()
    assert not bucket.try_acquire()


async def test_bucket_refills_over_time():
    bucket = TokenBucket(rate_per_sec=100, burst=1)
    await bucket.acquire()
    await bucket.acquire()  # waits ~10ms for a refill rather than failing
    assert bucket.available < 1.0


async def test_bucket_times_out_rather_than_waiting_forever():
    bucket = TokenBucket(rate_per_sec=0.5, burst=1)
    await bucket.acquire()
    with pytest.raises(RateLimitError, match="timed out"):
        await bucket.acquire(timeout=0.05)


async def test_asking_for_more_than_capacity_is_a_programming_error():
    bucket = TokenBucket(rate_per_sec=1, burst=1)
    with pytest.raises(ValueError, match="cannot acquire"):
        await bucket.acquire(5)


def test_bucket_rejects_a_nonsense_rate():
    with pytest.raises(ValueError, match="must be positive"):
        TokenBucket(rate_per_sec=0)


async def test_registry_without_a_default_does_not_limit():
    registry = RateLimiterRegistry()
    assert registry.get("anything") is None
    await registry.acquire("anything")  # no-op, must not raise


async def test_registry_creates_buckets_from_the_default_rate():
    registry = RateLimiterRegistry(default_rate_per_sec=1000)
    assert registry.get("openai") is registry.get("openai")


async def test_explicit_configuration_wins_over_the_default():
    registry = RateLimiterRegistry(default_rate_per_sec=1000)
    registry.configure("openai", rate_per_sec=10, burst=3)
    assert registry.get("openai").capacity == 3
