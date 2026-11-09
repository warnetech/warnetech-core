"""Exception hierarchy shared by the gateway and memory layers.

Provider-specific failures are normalised into these types so that downstream
code never has to catch an ``anthropic.APIError`` next to an ``openai.APIError``.
"""

from __future__ import annotations


class WarnetechError(Exception):
    """Base class for every error raised by warnetech-core."""


class ConfigError(WarnetechError):
    """Raised when configuration is missing or contradictory."""


class ProviderError(WarnetechError):
    """A call to an upstream model provider failed.

    Attributes:
        provider: Name of the provider that failed.
        status_code: HTTP status, when the failure came back over the wire.
        retryable: Whether retrying the same request could plausibly succeed.
    """

    def __init__(
        self,
        message: str,
        *,
        provider: str,
        status_code: int | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.provider = provider
        self.status_code = status_code
        self.retryable = retryable

    def __str__(self) -> str:  # pragma: no cover - trivial
        base = super().__str__()
        if self.status_code is None:
            return f"[{self.provider}] {base}"
        return f"[{self.provider} {self.status_code}] {base}"


class RateLimitError(ProviderError):
    """The provider (or our own local limiter) refused the request for now."""

    def __init__(
        self,
        message: str,
        *,
        provider: str,
        status_code: int | None = 429,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message, provider=provider, status_code=status_code, retryable=True)
        self.retry_after = retry_after


class TimeoutError_(ProviderError):
    """The provider did not respond in time."""

    def __init__(self, message: str, *, provider: str) -> None:
        super().__init__(message, provider=provider, retryable=True)


class AllProvidersFailed(WarnetechError):
    """Every model in the fallback chain failed.

    Attributes:
        failures: Ordered ``(model_alias, error)`` pairs, in the order tried.
    """

    def __init__(self, failures: list[tuple[str, BaseException]]) -> None:
        self.failures = failures
        detail = "; ".join(f"{alias}: {err}" for alias, err in failures)
        super().__init__(f"all candidate models failed -> {detail}")


class ModelNotRegistered(ConfigError):
    """A model alias was requested that the registry does not know about."""


class PromptNotFound(WarnetechError):
    """A prompt id (or a specific version of one) is not in the registry."""


class PromptRenderError(WarnetechError):
    """A prompt template was rendered with missing or extra variables."""


class MemoryError_(WarnetechError):
    """A memory/context store operation failed."""
