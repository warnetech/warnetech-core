"""Cost estimates.

These are a cached snapshot, dated by ``PRICES_AS_OF``. Treat the numbers as a
smoke alarm, not an invoice, and override them in ``[prices]`` in the config
when they drift. A model with no known price reports ``None`` rather than
``0.00``: a confident zero is worse than an honest "unknown".
"""

from __future__ import annotations

import math

PRICES_AS_OF = "2026-06-24"

# USD per 1M tokens: (input, output).
PRICES: dict[str, tuple[float, float]] = {
    "claude-fable-5": (10.00, 50.00),
    "claude-opus-5": (5.00, 25.00),
    "claude-opus-4": (5.00, 25.00),
    "claude-sonnet-5": (3.00, 15.00),
    "claude-sonnet-4": (3.00, 15.00),
    "claude-haiku-4-5": (1.00, 5.00),
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.00),
}

FREE_PROVIDER_TYPES = ("ollama", "fake")


def estimate_tokens(text: str) -> int:
    """~4 characters per token. Good enough to decide whether to worry."""
    return max(1, math.ceil(len(text) / 4))


def price_of(
    model: str, provider_type: str, overrides: dict[str, tuple[float, float]] | None = None
) -> tuple[float, float] | None:
    if provider_type in FREE_PROVIDER_TYPES:
        return (0.0, 0.0)
    table = {**PRICES, **(overrides or {})}
    for known in sorted(table, key=len, reverse=True):
        if model.startswith(known):
            return table[known]
    return None


def cost(price: tuple[float, float] | None, input_tokens: int, output_tokens: int) -> float | None:
    if price is None:
        return None
    return (input_tokens * price[0] + output_tokens * price[1]) / 1_000_000


def fmt_usd(value: float | None) -> str:
    if value is None:
        return "n/a (no price on file)"
    if value == 0:
        return "$0 (local)"
    return f"${value:.6f}" if value < 0.01 else f"${value:.4f}"
