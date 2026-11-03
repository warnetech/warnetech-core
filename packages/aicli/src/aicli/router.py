"""Routing: turn a request into a ranked list of candidates, then call them in order.

Two ways to build the list:

* **fixed** (default): the requested model, then ``fallback`` in order.
* **ranked** (``--route cheapest|fastest|best``): every model in
  ``[routing] pool`` that is available, passes ``--min-quality`` and
  ``--max-cost``, sorted by the strategy. The rest of the ranking becomes the
  fallback chain.

Either way, ``local_only`` removes every cloud candidate before anything is
called, and each decision (skip, rank, fallback) is reported to the caller, so
the routing is never a black box.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from . import pricing
from .audit import AuditLog, sha256
from .config import Config, ConfigError
from .providers import Completion, Message, Provider, ProviderError, ToolCall, ToolSpec, build

# Bare model names that unambiguously belong to one provider type.
_PREFIX_TO_TYPE = (("claude-", "anthropic"), ("gpt-", "openai"), ("o1", "openai"),
                   ("o3", "openai"), ("o4", "openai"))
STRATEGIES = ("cheapest", "fastest", "best")
DEFAULT_MAX_STEPS = 8


@dataclass(frozen=True)
class Target:
    provider: str
    model: str

    def __str__(self) -> str:
        return f"{self.provider}/{self.model}"


@dataclass
class Ranked:
    target: Target
    est_cost: float | None
    latency_ms: int | None
    quality: int | None


@dataclass
class RouteResult:
    completion: Completion
    target: Target
    attempts: list[tuple[Target, str]] = field(default_factory=list)  # failed hops

    @property
    def fell_back_from(self) -> Target | None:
        return self.attempts[0][0] if self.attempts else None


class AllProvidersFailed(Exception):
    def __init__(self, attempts: list[tuple[Target, str]]):
        self.attempts = attempts
        lines = "\n".join(f"  {t}: {why}" for t, why in attempts) or "  (no candidates left)"
        super().__init__(f"every candidate failed:\n{lines}")


class ToolLoopError(Exception):
    """A tool loop failed after tools had already run: not safe to retry elsewhere."""


class Router:
    def __init__(self, config: Config, audit: AuditLog | None = None,
                 notify: Callable[[str], None] | None = None):
        self.config = config
        self.audit = audit
        self.notify = notify or (lambda _msg: None)
        self._providers: dict[str, Provider] = {}

    # -- resolution -------------------------------------------------------
    def provider(self, name: str) -> Provider:
        if name not in self._providers:
            if name not in self.config.providers:
                raise ConfigError(
                    f"no provider named {name!r} (configured: {', '.join(self.config.providers)})"
                )
            self._providers[name] = build(self.config.providers[name])
        return self._providers[name]

    def resolve(self, model: str | None) -> Target:
        """``provider/model``, an alias, or a bare name with an obvious owner."""
        spec = model or self.config.default_model
        spec = self.config.aliases.get(spec, spec)
        if "/" in spec:
            head, rest = spec.split("/", 1)
            if head in self.config.providers:
                return Target(head, rest)
        for prefix, ptype in _PREFIX_TO_TYPE:
            if spec.startswith(prefix):
                for name, pcfg in self.config.providers.items():
                    if pcfg.type == ptype:
                        return Target(name, spec)
        raise ConfigError(
            f"cannot tell which provider serves {spec!r}; use provider/model, "
            f"e.g. local/{spec}, or add an alias"
        )

    def is_local(self, target: Target) -> bool:
        return self.config.providers[target.provider].is_local

    def provider_type(self, target: Target) -> str:
        return self.config.providers[target.provider].type

    def price(self, target: Target) -> tuple[float, float] | None:
        return pricing.price_of(target.model, self.provider_type(target), self.config.prices)

    def quality(self, target: Target) -> int | None:
        """Looked up under the full name, then any alias that points at it."""
        q = self.config.quality
        if str(target) in q:
            return q[str(target)]
        for alias, spec in self.config.aliases.items():
            if alias in q and self.resolve(spec) == target:
                return q[alias]
        return None

    # -- candidate lists --------------------------------------------------
    def _privacy_filter(self, chain: list[Target], requested: Target | None) -> list[Target]:
        if self.config.local_only:
            if requested is not None and not self.is_local(requested):
                raise ConfigError(f"local_only is on: refusing to send to {requested} "
                                  "(a cloud provider). Use a local model or --allow-cloud.")
            kept = [t for t in chain if self.is_local(t)]
            for t in chain:
                if t not in kept:
                    self.notify(f"- skipped {t}: local_only is on")
            return kept
        return chain

    def candidates(self, model: str | None) -> list[Target]:
        requested = self.resolve(model)
        chain = [requested]
        for spec in self.config.fallback:
            target = self.resolve(spec)
            if target not in chain:
                chain.append(target)
        return self._privacy_filter(chain, requested)

    def rank(self, strategy: str, prompt_tokens: int, max_tokens: int,
             min_quality: int | None = None, max_cost: float | None = None,
             profile: Callable[[Target], tuple[int | None, int | None]] | None = None,
             ) -> list[Ranked]:
        if strategy not in STRATEGIES:
            raise ConfigError(f"--route must be one of {', '.join(STRATEGIES)}")
        pool = self.config.routing_pool
        if not pool:
            raise ConfigError("--route needs a [routing] pool in the config")
        seen: list[Target] = []
        for spec in pool:
            target = self.resolve(spec)
            if target not in seen:
                seen.append(target)
        seen = self._privacy_filter(seen, None)

        ranked: list[Ranked] = []
        for target in seen:
            ok, why = self.provider(target.provider).available()
            if not ok:
                self.notify(f"- skipped {target}: {why}")
                continue
            quality = self.quality(target)
            if min_quality is not None and (quality is None or quality < min_quality):
                self.notify(f"- skipped {target}: quality {quality} < {min_quality}")
                continue
            latency, avg_out = profile(target) if profile else (None, None)
            # Expected output: what this model has produced before, else a quarter
            # of the ceiling. Used for ranking only; budgets use the ceiling.
            expect_out = avg_out if avg_out is not None else max(1, max_tokens // 4)
            est = pricing.cost(self.price(target), prompt_tokens, expect_out)
            if max_cost is not None:
                ceiling = pricing.cost(self.price(target), prompt_tokens, max_tokens)
                if ceiling is None or ceiling > max_cost:
                    self.notify(f"- skipped {target}: worst case {pricing.fmt_usd(ceiling)} "
                                f"> --max-cost ${max_cost}")
                    continue
            ranked.append(Ranked(target, est, latency, quality))

        inf = float("inf")
        if strategy == "cheapest":
            ranked.sort(key=lambda r: (inf if r.est_cost is None else r.est_cost,
                                       -(r.quality or 0)))
        elif strategy == "fastest":
            ranked.sort(key=lambda r: (inf if r.latency_ms is None else r.latency_ms,
                                       inf if r.est_cost is None else r.est_cost))
        else:
            ranked.sort(key=lambda r: (-(r.quality or 0),
                                       inf if r.est_cost is None else r.est_cost))
        return ranked

    # -- calling ----------------------------------------------------------
    def _audit_call(self, target: Target, provider: Provider, messages: list[Message],
                    status: str, completion: Completion | None = None,
                    error: str | None = None) -> None:
        if not self.audit:
            return
        body = "".join(str(m.get("content", "")) for m in messages)
        self.audit.write(
            "model_call", target=str(target), host=provider.host, local=self.is_local(target),
            bytes_out=len(body.encode()), prompt_sha256=sha256(body), status=status,
            input_tokens=completion.input_tokens if completion else None,
            output_tokens=completion.output_tokens if completion else None,
            error=error,
        )

    def _call(self, target: Target, messages: list[Message], max_tokens: int,
              system: str | None, params: dict[str, Any] | None,
              tools: list[ToolSpec] | None) -> Completion:
        provider = self.provider(target.provider)
        if not self.is_local(target):
            self.notify(f"⇢ sending to cloud: {provider.host} ({target})")
        try:
            c = provider.complete(messages, target.model, max_tokens, system, params, tools)
        except ProviderError as exc:
            self._audit_call(target, provider, messages, "error", error=str(exc))
            raise
        self._audit_call(target, provider, messages, "ok", c)
        return c

    def _tool_loop(self, target: Target, messages: list[Message], max_tokens: int,
                   system: str | None, params: dict[str, Any] | None, tools: list[ToolSpec],
                   run_tool: Callable[[ToolCall], str], max_steps: int) -> Completion:
        provider = self.provider(target.provider)
        msgs = list(messages)
        total_in = total_out = latency = 0
        tools_ran = False
        for step in range(1, max_steps + 1):
            try:
                c = self._call(target, msgs, max_tokens, system, params, tools)
            except ProviderError as exc:
                if tools_ran:
                    # Tools may have had side effects; replaying them elsewhere is unsafe.
                    raise ToolLoopError(f"{target} failed mid tool loop: {exc}") from exc
                raise
            total_in += c.input_tokens
            total_out += c.output_tokens
            latency += c.latency_ms
            if not c.tool_calls:
                c.input_tokens, c.output_tokens, c.latency_ms, c.steps = \
                    total_in, total_out, latency, step
                return c
            if c.raw_message:
                msgs.append(c.raw_message)
            results = [run_tool(call) for call in c.tool_calls]
            tools_ran = True
            msgs.extend(provider.tool_result_messages(c.tool_calls, results))
        c.text = (c.text + "\n\n" if c.text else "") + \
            f"[stopped: tool loop hit --max-steps {max_steps}]"
        c.stop_reason = "max_steps"
        c.input_tokens, c.output_tokens, c.latency_ms, c.steps = \
            total_in, total_out, latency, max_steps
        return c

    def complete(self, messages: list[Message], chain: list[Target],
                 max_tokens: int | None = None, system: str | None = None,
                 params: dict[str, Any] | None = None,
                 tools: list[ToolSpec] | None = None,
                 run_tool: Callable[[ToolCall], str] | None = None,
                 max_steps: int = DEFAULT_MAX_STEPS,
                 on_fallback: Callable[[Target, str], None] | None = None) -> RouteResult:
        attempts: list[tuple[Target, str]] = []
        limit = max_tokens or self.config.max_tokens
        for target in chain:
            try:
                if tools and run_tool:
                    completion = self._tool_loop(target, messages, limit, system, params,
                                                 tools, run_tool, max_steps)
                else:
                    completion = self._call(target, messages, limit, system, params, None)
            except ProviderError as exc:
                attempts.append((target, str(exc)))
                if on_fallback:
                    on_fallback(target, str(exc))
                continue
            return RouteResult(completion, target, attempts)
        raise AllProvidersFailed(attempts)
