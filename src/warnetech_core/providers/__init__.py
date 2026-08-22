"""Provider adapters. Import the concrete ones lazily so that a missing
optional dependency for one provider never breaks the others."""

from __future__ import annotations

from .base import Provider
from .echo import EchoProvider

__all__ = ["Provider", "EchoProvider", "AnthropicProvider", "OpenAIProvider"]


def __getattr__(name: str):  # pragma: no cover - thin import shim
    if name == "AnthropicProvider":
        from .anthropic import AnthropicProvider

        return AnthropicProvider
    if name == "OpenAIProvider":
        from .openai import OpenAIProvider

        return OpenAIProvider
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
