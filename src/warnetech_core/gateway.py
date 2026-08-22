"""The AI gateway.

Every AI call in every Warnetech project goes through :class:`AIGateway`. It
owns the four things that would otherwise be reimplemented (differently) in each
project:

* **model abstraction** — callers name a task alias, never a provider model id;
* **prompt templates** — versioned, rendered strictly, recorded against usage;
* **cost tracking** — every call attributed to a project and team;
* **resilience** — rate limiting, retry with backoff, cross-provider fallback.

Adapters stay dumb on purpose: they translate one request and raise. All the
policy lives here, so it behaves identically no matter who is upstream.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Sequence
from typing import Any

from .errors import (
    AllProvidersFailed,
    ConfigError,
    ModelNotRegistered,
    ProviderError,
)
from .prompts import PromptRegistry
from .providers.base import Provider
from .registry import ModelRegistry, ModelSpec
from .resilience import RateLimiterRegistry, RetryPolicy, is_retryable
from .types import (
    CompletionRequest,
    CompletionResponse,
    EmbeddingRequest,
    EmbeddingResponse,
    Message,
)
from .usage import UsageRecord, UsageTracker

log = logging.getLogger("warnetech.gateway")


class AIGateway:
    """Single entry point for model calls.

    Example:
        >>> gateway = AIGateway.offline()                      # doctest: +SKIP
        >>> reply = await gateway.complete(                    # doctest: +SKIP
        ...     "chat.default",
        ...     [Message.user("hello")],
        ...     project="hub-notes",
        ... )
    """

    def __init__(
        self,
        providers: dict[str, Provider] | None = None,
        *,
        registry: ModelRegistry | None = None,
        prompts: PromptRegistry | None = None,
        usage: UsageTracker | None = None,
        retry: RetryPolicy | None = None,
        limiters: RateLimiterRegistry | None = None,
        default_project: str = "default",
        enable_fallback: bool = True,
        max_fallbacks: int = 3,
    ) -> None:
        self.providers: dict[str, Provider] = dict(providers or {})
        self.registry = registry or ModelRegistry.default()
        self.prompts = prompts or PromptRegistry()
        self.usage = usage or UsageTracker()
        self.retry = retry or RetryPolicy()
        self.limiters = limiters or RateLimiterRegistry()
        self.default_project = default_project
        self.enable_fallback = enable_fallback
        self.max_fallbacks = max_fallbacks

    # ---- construction ---------------------------------------------------

    @classmethod
    def offline(cls, **kwargs: Any) -> AIGateway:
        """Gateway wired to the deterministic echo provider only.

        Lets a project run its full code path — prompts, memory, accounting —
        with no API keys at all.
        """
        from .providers.echo import EchoProvider

        registry = kwargs.pop("registry", None) or ModelRegistry.default()
        registry.use_offline_models()
        return cls({"echo": EchoProvider()}, registry=registry, **kwargs)

    @classmethod
    def from_env(cls, **kwargs: Any) -> AIGateway:
        """Register every provider whose API key is present in the environment.

        The echo provider is always registered, so offline aliases keep working
        even in a fully-configured deployment.
        """
        import os

        from .providers.echo import EchoProvider

        providers: dict[str, Provider] = {"echo": EchoProvider()}
        if os.getenv("ANTHROPIC_API_KEY"):
            from .providers.anthropic import AnthropicProvider

            providers["anthropic"] = AnthropicProvider()
        if os.getenv("OPENAI_API_KEY"):
            from .providers.openai import OpenAIProvider

            providers["openai"] = OpenAIProvider()
        if len(providers) == 1:
            log.warning(
                "no provider API keys found; only the offline `echo` provider is available"
            )
        return cls(providers, **kwargs)

    def register_provider(self, provider: Provider) -> None:
        self.providers[provider.name] = provider

    # ---- completions ----------------------------------------------------

    async def complete(
        self,
        model: str,
        messages: Sequence[Message] | str,
        *,
        project: str | None = None,
        team: str | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.7,
        stop: list[str] | None = None,
        system: str | None = None,
        metadata: dict[str, Any] | None = None,
        prompt_id: str | None = None,
        prompt_version: int | None = None,
    ) -> CompletionResponse:
        """Run a chat completion against ``model`` (a registry alias).

        Args:
            model: Task alias (``chat.default``) or concrete alias.
            messages: Message list, or a bare string treated as one user turn.
            system: Optional system prompt prepended to ``messages``.

        Raises:
            AllProvidersFailed: every model in the fallback chain failed.
            BudgetExceeded: the project or team is over its spend cap.
        """
        turns = [Message.user(messages)] if isinstance(messages, str) else list(messages)
        if system:
            turns = [Message.system(system), *turns]
        request = CompletionRequest(
            model=model,
            messages=turns,
            max_tokens=max_tokens,
            temperature=temperature,
            stop=stop,
            project=project or self.default_project,
            team=team,
            prompt_id=prompt_id,
            prompt_version=prompt_version,
            metadata=metadata or {},
        )
        return await self.complete_request(request)

    async def complete_request(self, request: CompletionRequest) -> CompletionResponse:
        """Run a fully-formed :class:`CompletionRequest`."""
        self.usage.check_budget(project=request.project, team=request.team)
        chain = self._chain(request.model, modality="chat")
        return await self._dispatch(
            chain,
            operation="complete",
            request=request,
            call=lambda provider, spec: provider.complete(request, spec),
            project=request.project,
            team=request.team,
            prompt_id=request.prompt_id,
            prompt_version=request.prompt_version,
            metadata=request.metadata,
        )

    async def complete_prompt(
        self,
        prompt_id: str,
        variables: dict[str, Any] | None = None,
        *,
        version: int | None = None,
        model: str | None = None,
        **kwargs: Any,
    ) -> CompletionResponse:
        """Render a registered prompt template and run it.

        The resulting usage row carries ``prompt_id``/``prompt_version``, so
        cost can be attributed to a specific prompt version later.
        """
        template = self.prompts.get(prompt_id, version)
        messages = template.render(variables)
        chosen = model or template.model
        if chosen is None:
            raise ConfigError(
                f"prompt {template.ref} declares no `model` and none was passed to "
                f"complete_prompt()"
            )
        kwargs.setdefault("metadata", {})
        kwargs["metadata"] = {**kwargs["metadata"], "prompt_checksum": template.checksum}
        return await self.complete(
            chosen,
            messages,
            prompt_id=template.id,
            prompt_version=template.version,
            **kwargs,
        )

    # ---- embeddings -----------------------------------------------------

    async def embed(
        self,
        model: str,
        inputs: Sequence[str] | str,
        *,
        project: str | None = None,
        team: str | None = None,
    ) -> EmbeddingResponse:
        """Embed one string or a batch."""
        texts = [inputs] if isinstance(inputs, str) else list(inputs)
        request = EmbeddingRequest(
            model=model,
            inputs=texts,
            project=project or self.default_project,
            team=team,
        )
        self.usage.check_budget(project=request.project, team=request.team)
        chain = self._chain(request.model, modality="embedding")
        return await self._dispatch(
            chain,
            operation="embed",
            request=request,
            call=lambda provider, spec: provider.embed(request, spec),
            project=request.project,
            team=request.team,
        )

    # ---- dispatch core --------------------------------------------------

    def _chain(self, model: str, *, modality: str) -> list[ModelSpec]:
        """Resolve a model to its candidate chain, filtered to usable entries."""
        chain = self.registry.chain(
            model, max_length=1 + (self.max_fallbacks if self.enable_fallback else 0)
        )
        primary = chain[0]
        if primary.modality != modality:
            raise ConfigError(
                f"model {model!r} resolves to {primary.alias} which is a {primary.modality} "
                f"model, but a {modality} model was requested"
            )
        # Drop candidates whose provider is not registered, or whose modality
        # does not match — a fallback we cannot call is not a fallback.
        usable = [
            spec
            for spec in chain
            if spec.provider in self.providers and spec.modality == modality
        ]
        if not usable:
            registered = ", ".join(sorted(self.providers)) or "none"
            raise ModelNotRegistered(
                f"no usable provider for {model!r} (wanted {primary.provider!r}, "
                f"registered: {registered})"
            )
        return usable

    async def _dispatch(
        self,
        chain: list[ModelSpec],
        *,
        operation: str,
        request: Any,
        call: Any,
        project: str,
        team: str | None,
        prompt_id: str | None = None,
        prompt_version: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Any:
        """Try each model in the chain, retrying retryable failures within each.

        Records exactly one usage row per call — on the success, or on the final
        failure of each candidate, so a burned-and-failed call still shows up in
        the bill.
        """
        failures: list[tuple[str, BaseException]] = []
        tried: list[str] = []
        started_all = time.perf_counter()

        for spec in chain:
            provider = self.providers[spec.provider]
            attempts = 0
            last_error: BaseException | None = None

            while attempts < self.retry.max_attempts:
                attempts += 1
                try:
                    await self.limiters.acquire(spec.provider)
                    result = await call(provider, spec)
                except Exception as exc:  # noqa: BLE001 - normalised just below
                    last_error = exc
                    retry_after = getattr(exc, "retry_after", None)
                    if not is_retryable(exc) or attempts >= self.retry.max_attempts:
                        break
                    delay = self.retry.delay_for(attempts + 1, retry_after=retry_after)
                    log.warning(
                        "%s via %s failed (attempt %d/%d): %s — retrying in %.2fs",
                        spec.alias,
                        spec.provider,
                        attempts,
                        self.retry.max_attempts,
                        exc,
                        delay,
                    )
                    await asyncio.sleep(delay)
                    continue

                result.attempts = attempts
                if tried:
                    result.fell_back_from = list(tried)
                self._record(
                    operation=operation,
                    spec=spec,
                    project=project,
                    team=team,
                    input_tokens=result.usage.input_tokens,
                    output_tokens=result.usage.output_tokens,
                    cost_usd=result.cost_usd,
                    latency_ms=result.latency_ms,
                    attempts=attempts,
                    ok=True,
                    error=None,
                    prompt_id=prompt_id,
                    prompt_version=prompt_version,
                    metadata=metadata,
                )
                return result

            assert last_error is not None
            failures.append((spec.alias, last_error))
            tried.append(spec.alias)
            self._record(
                operation=operation,
                spec=spec,
                project=project,
                team=team,
                latency_ms=(time.perf_counter() - started_all) * 1000,
                attempts=attempts,
                ok=False,
                error=f"{type(last_error).__name__}: {last_error}",
                prompt_id=prompt_id,
                prompt_version=prompt_version,
                metadata=metadata,
            )
            if not self._should_fall_back(last_error):
                break
            log.warning("falling back from %s after: %s", spec.alias, last_error)

        raise AllProvidersFailed(failures)

    def _should_fall_back(self, error: BaseException) -> bool:
        """Whether a different provider is worth trying after this failure.

        A 400 means our request was malformed — the next provider will reject it
        too, so failing fast beats burning the chain.
        """
        if not self.enable_fallback:
            return False
        if isinstance(error, ProviderError) and error.status_code is not None:
            if 400 <= error.status_code < 500 and error.status_code not in (408, 409, 429):
                return False
        return True

    def _record(
        self,
        *,
        operation: str,
        spec: ModelSpec,
        project: str,
        team: str | None,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cost_usd: float = 0.0,
        latency_ms: float = 0.0,
        attempts: int = 1,
        ok: bool = True,
        error: str | None = None,
        prompt_id: str | None = None,
        prompt_version: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.usage.record(
            UsageRecord(
                project=project,
                team=team,
                operation=operation,
                model_alias=spec.alias,
                provider=spec.provider,
                provider_model=spec.provider_model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_usd=cost_usd,
                latency_ms=latency_ms,
                attempts=attempts,
                ok=ok,
                error=error,
                prompt_id=prompt_id,
                prompt_version=prompt_version,
                metadata=metadata or {},
            )
        )

    # ---- lifecycle ------------------------------------------------------

    async def aclose(self) -> None:
        """Close every provider's connection pool."""
        for provider in self.providers.values():
            await provider.aclose()

    async def __aenter__(self) -> AIGateway:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()
