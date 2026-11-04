"""Ensemble: draw k candidates, then choose between them.

**Claim.** When verifying an answer is cheaper than producing one, spending a
fixed budget on *k samples plus selection* beats spending it all on one very
careful sample. Code is the ideal case: generation is expensive and open-ended,
while "does the test pass" is nearly free.

Three selectors, in increasing order of what they cost and what they buy:

* :func:`self_consistency` -- cluster identical/near-identical answers and take
  the plurality. Free. Works when the answer space is narrow (a classification,
  a number, a short extraction). Useless for prose, where no two samples ever
  match.
* :func:`best_of_n` with a *programmatic* verifier -- run the tests, keep what
  passes. This is the version that actually earns its cost.
* :func:`best_of_n` with a *model* verifier -- a judge picks. Weakest link: the
  judge shares the generator's blind spots, and it will happily prefer the
  most confident wrong answer. Give it a rubric and cross-check it.

**How it would fail.** Correlated errors. k samples from one model at one
temperature are not k independent draws; if the model misreads the prompt, it
misreads it every time and the ensemble converts one wrong answer into a
confident wrong answer. Diversify what you can -- vary effort, vary the framing,
vary the model -- and treat unanimous agreement on a hard question as
suspicious rather than reassuring.

**Measurement.** pass@1 of best-of-k against pass@1 of a single sample at k-times
the effort, at equal dollar spend. If they tie, the extra latency is a loss.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

from ..trace import NULL_TRACER, Tracer, cost_usd
from ..types import Request, Response

Scorer = Callable[[str], float]
"""Scores one candidate. Higher is better. Return -inf to reject outright."""


@dataclass(slots=True)
class Candidate:
    text: str
    index: int
    score: float = 0.0
    detail: str = ""
    response: Response | None = None


@dataclass(slots=True)
class EnsembleResult:
    winner: str
    candidates: list[Candidate] = field(default_factory=list)
    agreement: float = 0.0
    """Share of candidates that clustered with the winner. Confidence, roughly."""
    cost_usd: float = 0.0
    selector: str = ""

    @property
    def unanimous(self) -> bool:
        return self.agreement >= 1.0

    @property
    def contested(self) -> bool:
        """No option cleared half the votes -- a good trigger to escalate."""
        return self.agreement < 0.5


def _sample(
    provider: Any,
    request: Request,
    k: int,
    *,
    parallel: bool,
    vary: Sequence[dict[str, Any]] | None,
) -> list[Response]:
    """Draw k samples, optionally varying the request to decorrelate them."""
    requests = []
    for i in range(k):
        overrides = dict(vary[i % len(vary)]) if vary else {}
        overrides.setdefault("metadata", {**request.metadata, "sample": i})
        requests.append(request.with_(**overrides))

    if not parallel:
        return [provider.complete(r) for r in requests]
    with ThreadPoolExecutor(max_workers=min(k, 8)) as pool:
        return list(pool.map(provider.complete, requests))


def best_of_n(
    provider: Any,
    prompt: str,
    *,
    score: Scorer,
    k: int = 4,
    model: str = "claude-opus-5",
    system: str | None = None,
    max_tokens: int = 8000,
    effort: str | None = None,
    vary: Sequence[dict[str, Any]] | None = None,
    parallel: bool = True,
    tracer: Tracer | None = None,
) -> EnsembleResult:
    """Draw k candidates and keep the highest-scoring one.

    ``vary`` decorrelates the draws -- pass e.g.
    ``[{"effort": "medium"}, {"effort": "high"}, {"model": "claude-sonnet-5"}]``.
    Correlated samples are the main way this technique quietly stops working.

    >>> from llmforge.provider import FakeProvider
    >>> provider = FakeProvider(["short", "a much longer answer", "mid"])
    >>> best_of_n(provider, "go", score=len, k=3, parallel=False).winner
    'a much longer answer'
    """
    tracer = tracer or NULL_TRACER
    base = Request(
        messages=[{"role": "user", "content": prompt}],
        model=model,
        system=system,
        max_tokens=max_tokens,
        effort=effort,  # type: ignore[arg-type]
        metadata={"lab": "ensemble"},
    )

    with tracer.span("ensemble.sample", kind="stage", k=k):
        responses = _sample(provider, base, k, parallel=parallel, vary=vary)

    candidates: list[Candidate] = []
    cost = 0.0
    for i, response in enumerate(responses):
        cost += cost_usd(response.model or model, response.usage)
        candidates.append(
            Candidate(text=response.text, index=i, score=score(response.text), response=response)
        )

    candidates.sort(key=lambda c: -c.score)
    best = candidates[0]
    ties = sum(1 for c in candidates if c.score == best.score)

    return EnsembleResult(
        winner=best.text,
        candidates=candidates,
        agreement=ties / len(candidates),
        cost_usd=cost,
        selector="score",
    )


def normalize(text: str) -> str:
    """Canonical form for agreement clustering.

    Deliberately aggressive: lowercase, collapse whitespace, drop terminal
    punctuation. Two answers that differ only in formatting are the same answer,
    and treating them as different is what makes naive self-consistency report
    0% agreement on everything.
    """
    return re.sub(r"\s+", " ", text.strip().lower()).rstrip(".!,;:")


def self_consistency(
    provider: Any,
    prompt: str,
    *,
    k: int = 5,
    model: str = "claude-opus-5",
    system: str | None = None,
    max_tokens: int = 2000,
    extract: Callable[[str], str] = normalize,
    vary: Sequence[dict[str, Any]] | None = None,
    parallel: bool = True,
    tracer: Tracer | None = None,
) -> EnsembleResult:
    """Take the plurality answer across k samples.

    ``extract`` reduces a response to the thing being voted on -- for a
    classification that is the label, for arithmetic the final number. Voting on
    raw prose produces k clusters of size 1 and tells you nothing.

    The useful output is often not ``winner`` but ``agreement``: low agreement
    is a reliable signal that the question is ambiguous or the model is
    guessing, and it is a better escalation trigger than any confidence score a
    model will state about itself.

    >>> from llmforge.provider import FakeProvider
    >>> r = self_consistency(FakeProvider(["yes", "yes", "no"]), "?", k=3, parallel=False)
    >>> r.winner, round(r.agreement, 2)
    ('yes', 0.67)
    """
    tracer = tracer or NULL_TRACER
    base = Request(
        messages=[{"role": "user", "content": prompt}],
        model=model,
        system=system,
        max_tokens=max_tokens,
        metadata={"lab": "ensemble", "selector": "self_consistency"},
    )

    with tracer.span("ensemble.vote", kind="stage", k=k):
        responses = _sample(provider, base, k, parallel=parallel, vary=vary)

    keys = [extract(r.text) for r in responses]
    votes = Counter(keys)
    winning_key, count = votes.most_common(1)[0]
    winner = next(r.text for r, key in zip(responses, keys, strict=True) if key == winning_key)

    return EnsembleResult(
        winner=winner,
        candidates=[
            Candidate(text=r.text, index=i, score=votes[key], detail=key, response=r)
            for i, (r, key) in enumerate(zip(responses, keys, strict=True))
        ],
        agreement=count / len(responses),
        cost_usd=sum(cost_usd(r.model or model, r.usage) for r in responses),
        selector="self_consistency",
    )


JUDGE_SYSTEM = """\
You are selecting between candidate answers. Reply with the integer index of the \
best candidate and nothing else.

Judge on correctness against the task first, then on completeness, then on \
concision. Do not reward length, confidence, or formatting. If two candidates \
are equally correct, prefer the shorter one.\
"""


def judge_scorer(
    provider: Any,
    prompt: str,
    candidates: Sequence[str],
    *,
    model: str = "claude-opus-5",
) -> int:
    """Ask a model to pick the best candidate. Returns an index.

    The weakest selector in this module and the one to reach for last. A judge
    drawn from the same model family shares the generator's blind spots -- it
    cannot see an error it would also have made. Prefer a programmatic verifier
    wherever one exists, and where one does not, at least cross-check the judge
    against human labels before trusting it.
    """
    listing = "\n\n".join(f"<candidate index=\"{i}\">\n{c}\n</candidate>"
                          for i, c in enumerate(candidates))
    response = provider.complete(
        Request(
            messages=[{"role": "user", "content": f"<task>\n{prompt}\n</task>\n\n{listing}"}],
            model=model,
            system=JUDGE_SYSTEM,
            max_tokens=16,
            metadata={"lab": "ensemble", "role": "judge"},
        )
    )
    match = re.search(r"\d+", response.text)
    index = int(match.group()) if match else 0
    return index if 0 <= index < len(candidates) else 0
