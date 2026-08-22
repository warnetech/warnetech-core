"""Shared fixtures. Everything runs offline — no network, no API keys."""

from __future__ import annotations

import pytest

from warnetech_core import AIGateway, MemoryService, PromptRegistry
from warnetech_core.memory.inmemory import InMemoryContextStore
from warnetech_core.memory.sqlite import SQLiteContextStore
from warnetech_core.resilience import RetryPolicy
from warnetech_core.usage import InMemoryUsageStore, UsageTracker


@pytest.fixture
def prompts() -> PromptRegistry:
    registry = PromptRegistry()
    registry.add(
        "greet",
        [("system", "You are terse."), ("user", "Say hello to {{ name }}.")],
        version=1,
        model="chat.default",
    )
    registry.add(
        "greet",
        [("system", "You are terse."), ("user", "Greet {{ name }} warmly.")],
        version=2,
        model="chat.default",
    )
    return registry


@pytest.fixture
def gateway(prompts: PromptRegistry) -> AIGateway:
    return AIGateway.offline(
        prompts=prompts,
        usage=UsageTracker(InMemoryUsageStore()),
        # Keep retries instant so failure-path tests do not sleep.
        retry=RetryPolicy(max_attempts=3, initial_delay=0.001, max_delay=0.002, jitter=False),
    )


@pytest.fixture(params=["memory", "sqlite"])
def store(request: pytest.FixtureRequest):
    """Both built-in stores, so conformance is proven for each."""
    if request.param == "memory":
        return InMemoryContextStore()
    return SQLiteContextStore(":memory:")


@pytest.fixture
def memory(gateway: AIGateway, store) -> MemoryService:
    return MemoryService(gateway, store)
