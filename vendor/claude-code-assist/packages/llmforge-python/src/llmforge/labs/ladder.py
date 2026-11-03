"""Ladder: escalate from cheap to strong only when a verifier says to.

**Claim.** Most requests in a production workload are easy. Routing all of them
to the strongest model pays the hard-case price on every case. If you have a
verifier that is cheaper than generation, a ladder -- try Haiku, check, try
Sonnet, check, try Opus -- lands near top-model quality at a fraction of the
spend, because the expensive rung only runs on the cases that need it.

The whole technique lives or dies on one thing: **the verifier must be
trustworthy in the negative direction.** A verifier that wrongly says "good" on
a bad answer silently ships the cheap model's mistake, and you will not see it
in aggregate metrics. A verifier that wrongly says "bad" merely costs money.
So: prefer verifiers that can only fail closed (tests, compilers, schema
validation, invariant checks) over verifiers that opine (a judge model).

**Safety-critical note.** Where an error is expensive to reverse, do not ladder
at all, or set ``require_top_rung=True`` so the strongest model must sign off
even when a cheaper rung passed. Saving four cents is not a reason to accept a
different risk profile on a path that can hurt someone.

**How it would fail.** Verifier miscalibration, and distribution shift -- the
mix of easy to hard cases moves, the escalation rate moves with it, and the
savings you budgeted for evaporate. Track escalation rate as a first-class
metric, not a footnote.

**Measurement.** Quality parity against always-top-model on a held-out suite,
plus realized cost per accepted answer, plus the false-accept rate of the
verifier measured against human labels.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ..trace import NULL_TRACER, Tracer, cost_usd
from ..types import Request

Verifier = Callable[[str], tuple[bool, str]]
"""Checks one candidate answer. Returns (accepted, reason)."""


@dataclass(slots=True)
class Rung:
    """One step on the ladder."""

    model: str
    effort: str | None = None
    max_tokens: int = 8000
    label: str = ""

    def __post_init__(self) -> None:
        if not self.label:
            self.label = f"{self.model}/{self.effort or 'default'}"


DEFAULT_LADDER: tuple[Rung, ...] = (
    Rung("claude-haiku-4-5", max_tokens=4000),
    Rung("claude-sonnet-5", effort="medium"),
    Rung("claude-opus-5", effort="high"),
)


@dataclass(slots=True)
class Attempt:
    rung: Rung
    text: str
    accepted: bool
    reason: str
    cost_usd: float


@dataclass(slots=True)
class LadderResult:
    text: str
    accepted: bool
    attempts: list[Attempt] = field(default_factory=list)
    cost_usd: float = 0.0

    @property
    def rungs_used(self) -> int:
        return len(self.attempts)

    @property
    def escalated(self) -> bool:
        return self.rungs_used > 1

    @property
    def settled_on(self) -> str:
        return self.attempts[-1].rung.label if self.attempts else ""

    def savings_vs(self, top_rung_cost: float) -> float:
        """Dollars saved against always running the top rung. Can be negative.

        Negative is the interesting case and the reason this method exists: a
        ladder that escalates most of the time costs *more* than going straight
        to the strong model, because you paid for the failed rungs too.
        """
        return top_rung_cost - self.cost_usd


def escalate(
    provider: Any,
    prompt: str,
    *,
    verify: Verifier,
    rungs: tuple[Rung, ...] = DEFAULT_LADDER,
    system: str | None = None,
    require_top_rung: bool = False,
    tracer: Tracer | None = None,
) -> LadderResult:
    """Walk the ladder until the verifier accepts, or the rungs run out.

    ``require_top_rung`` forces the strongest rung to also produce and verify an
    answer even when a cheaper one passed. Use it on paths where a wrong answer
    is expensive to reverse -- you give up the savings and keep the ladder's
    other benefit, which is that you now have two independent answers to compare.

    >>> from llmforge.provider import FakeProvider
    >>> provider = FakeProvider(["nope", "nope", "GOOD"])
    >>> check = lambda t: (t == "GOOD", "wanted GOOD")
    >>> r = escalate(provider, "task", verify=check)
    >>> r.accepted, r.rungs_used
    (True, 3)
    """
    tracer = tracer or NULL_TRACER
    attempts: list[Attempt] = []
    total = 0.0
    winner = ""
    accepted = False

    for rung in rungs:
        with tracer.span("ladder.attempt", kind="stage", rung=rung.label):
            response = provider.complete(
                Request(
                    messages=[{"role": "user", "content": prompt}],
                    model=rung.model,
                    system=system,
                    max_tokens=rung.max_tokens,
                    effort=rung.effort,  # type: ignore[arg-type]
                    metadata={"lab": "ladder", "rung": rung.label},
                )
            )
            cost = cost_usd(response.model or rung.model, response.usage)
            total += cost
            ok, reason = verify(response.text)
            attempts.append(Attempt(rung, response.text, ok, reason, cost))

        if ok:
            winner, accepted = response.text, True
            is_top = rung is rungs[-1]
            if not require_top_rung or is_top:
                break
            # require_top_rung: keep climbing, but we already have an answer.

    if not accepted and attempts:
        # Nothing verified. Return the top rung's attempt and say so plainly --
        # returning a rejected answer as if it passed is the one failure mode
        # this whole module exists to avoid.
        winner = attempts[-1].text

    return LadderResult(text=winner, accepted=accepted, attempts=attempts, cost_usd=total)
