"""Tracing and cost accounting.

An LLM feature you cannot see inside of is a feature you cannot improve. This
module gives every call a span, every span a parent, and every run a bill.

Design constraints that shaped it:

* **No dependencies.** Spans serialize to JSONL; anything can read JSONL.
* **Cheap when off.** A disabled tracer is a couple of attribute lookups.
* **Costs are estimates.** Prices below are a cached snapshot (see
  ``PRICES_AS_OF``). Treat the ledger as a smoke alarm, not an invoice.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .types import Usage

PRICES_AS_OF = "2026-06-24"

# USD per 1M tokens: (input, output). Cache writes bill at ~1.25x input,
# cache reads at ~0.1x input.
PRICES: dict[str, tuple[float, float]] = {
    "claude-fable-5": (10.00, 50.00),
    "claude-mythos-5": (10.00, 50.00),
    "claude-opus-5": (5.00, 25.00),
    "claude-opus-4-8": (5.00, 25.00),
    "claude-opus-4-7": (5.00, 25.00),
    "claude-opus-4-6": (5.00, 25.00),
    "claude-sonnet-5": (3.00, 15.00),
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-haiku-4-5": (1.00, 5.00),
}

CACHE_WRITE_MULTIPLIER = 1.25
CACHE_READ_MULTIPLIER = 0.10


def price_of(model: str) -> tuple[float, float]:
    """Input/output price per 1M tokens, longest-prefix matched.

    Unknown models price at Opus rates rather than zero: a silent 0.00 in a cost
    report is worse than a conservative over-estimate.
    """
    for known in sorted(PRICES, key=len, reverse=True):
        if model.startswith(known):
            return PRICES[known]
    return PRICES["claude-opus-5"]


def cost_usd(model: str, usage: Usage) -> float:
    """Estimated dollar cost of one call, cache tiers included."""
    inp, out = price_of(model)
    per_token_in = inp / 1_000_000
    return (
        usage.input_tokens * per_token_in
        + usage.cache_creation_input_tokens * per_token_in * CACHE_WRITE_MULTIPLIER
        + usage.cache_read_input_tokens * per_token_in * CACHE_READ_MULTIPLIER
        + usage.output_tokens * (out / 1_000_000)
    )


@dataclass
class Span:
    """One unit of work: a model call, a tool execution, a lab stage."""

    name: str
    kind: str = "call"
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    parent_id: str | None = None
    started_at: float = field(default_factory=time.time)
    ended_at: float | None = None
    attrs: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    @property
    def duration_ms(self) -> float:
        end = self.ended_at if self.ended_at is not None else time.time()
        return (end - self.started_at) * 1000

    def to_json(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["duration_ms"] = round(self.duration_ms, 2)
        return payload


class Tracer:
    """Collects spans, tallies cost, and optionally streams JSONL to disk.

    >>> from llmforge.types import Usage
    >>> tracer = Tracer(enabled=True)
    >>> with tracer.span("plan", kind="stage"):
    ...     tracer.record_call("claude-opus-5", Usage(input_tokens=100, output_tokens=50))
    >>> round(tracer.total_cost_usd, 6)
    0.00175
    """

    def __init__(self, sink: str | Path | None = None, *, enabled: bool = True) -> None:
        self.enabled = enabled
        self.spans: list[Span] = []
        self.usage_by_model: dict[str, Usage] = {}
        self._stack: list[Span] = []
        self._fh = None
        path = sink or os.getenv("LLMFORGE_TRACE")
        if path and enabled:
            p = Path(path)
            p.parent.mkdir(parents=True, exist_ok=True)
            self._fh = p.open("a", encoding="utf-8")

    # -- spans -------------------------------------------------------------- #

    @contextmanager
    def span(self, name: str, *, kind: str = "call", **attrs: Any) -> Iterator[Span]:
        """Open a span. Nesting is implied by the ``with`` structure."""
        span = Span(
            name=name,
            kind=kind,
            parent_id=self._stack[-1].id if self._stack else None,
            attrs=dict(attrs),
        )
        if not self.enabled:
            yield span
            return
        self._stack.append(span)
        try:
            yield span
        except Exception as exc:
            span.error = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            span.ended_at = time.time()
            self._stack.pop()
            self.spans.append(span)
            self._emit(span)

    def _emit(self, span: Span) -> None:
        if self._fh is None:
            return
        self._fh.write(json.dumps(span.to_json(), default=str) + "\n")
        self._fh.flush()

    # -- cost --------------------------------------------------------------- #

    def record_call(self, model: str, usage: Usage) -> None:
        """Fold one call's usage into the ledger and onto the current span."""
        prior = self.usage_by_model.get(model, Usage())
        self.usage_by_model[model] = prior + usage
        if self._stack:
            attrs = self._stack[-1].attrs
            attrs["model"] = model
            attrs["input_tokens"] = usage.total_input
            attrs["output_tokens"] = usage.output_tokens
            attrs["cache_hit_rate"] = round(usage.cache_hit_rate, 3)
            attrs["cost_usd"] = round(cost_usd(model, usage), 6)

    @property
    def total_cost_usd(self) -> float:
        return sum(cost_usd(m, u) for m, u in self.usage_by_model.items())

    @property
    def total_usage(self) -> Usage:
        total = Usage()
        for u in self.usage_by_model.values():
            total = total + u
        return total

    def summary(self) -> dict[str, Any]:
        """A dict suitable for printing at the end of a run or a CI job."""
        total = self.total_usage
        return {
            "spans": len(self.spans),
            "calls": sum(1 for s in self.spans if s.kind == "call"),
            "errors": sum(1 for s in self.spans if s.error),
            "input_tokens": total.total_input,
            "output_tokens": total.output_tokens,
            "cache_hit_rate": round(total.cache_hit_rate, 3),
            "cost_usd": round(self.total_cost_usd, 4),
            "wall_ms": round(sum(s.duration_ms for s in self.spans if s.parent_id is None), 1),
            "by_model": {
                m: round(cost_usd(m, u), 4) for m, u in sorted(self.usage_by_model.items())
            },
            "prices_as_of": PRICES_AS_OF,
        }

    def close(self) -> None:
        if self._fh is not None:
            self._fh.close()
            self._fh = None


NULL_TRACER = Tracer(enabled=False)
"""A tracer that records nothing. The default wherever a tracer is optional."""


class TracedProvider:
    """Wrap any provider so every call becomes a span in ``tracer``.

    This is the one line that turns an opaque integration into an observable
    one, and it is why cost regressions show up in review instead of on the
    invoice.
    """

    def __init__(self, inner: Any, tracer: Tracer, *, label: str = "llm") -> None:
        self.inner = inner
        self.tracer = tracer
        self.label = label
        self.name = f"traced({getattr(inner, 'name', 'provider')})"

    def complete(self, request: Any) -> Any:
        with self.tracer.span(self.label, kind="call", **request.metadata) as span:
            response = self.inner.complete(request)
            self.tracer.record_call(response.model or request.model, response.usage)
            span.attrs["stop_reason"] = response.stop_reason
            return response
