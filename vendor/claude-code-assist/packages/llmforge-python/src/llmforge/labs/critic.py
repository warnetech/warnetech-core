"""Critic: a separate pass whose only job is to find what is wrong.

**Claim.** Generation and criticism pull in opposite directions. A model asked
to produce an answer is optimizing for a plausible, complete-looking artifact;
the same model asked *only* to attack an existing artifact, against a fixed
rubric, finds defects the generating pass glossed over. Separating the two roles
into different turns -- with different system prompts -- recovers some of that.

**The part people get wrong.** "Reflection" loops are usually run open-ended,
and they degrade. Round one finds real bugs. Round two finds smaller ones. Round
three starts inventing problems to justify its existence, and the code gets
worse while every round reports progress. This implementation therefore:

* caps rounds (default 2, and that default is the finding, not a placeholder);
* requires the critic to emit *structured, severity-tagged* findings, so
  "consider adding a comment" cannot masquerade as a defect;
* stops as soon as a round produces no finding above ``min_severity``;
* detects oscillation -- if a revision reverts to a previous state, the loop is
  chasing its own tail and stops.

**How it would fail.** On tasks with a cheap ground truth (tests, a type
checker, a linter) the critic is strictly worse than just running the checker:
it is slower, costs more, and is less reliable. Reach for it only where no
mechanical check exists -- API design, error-message quality, migration safety,
prose.

**Measurement.** Human-labelled defect counts before and after, plus a
regression check that round N+1 never lowers the score of round N.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from ..trace import NULL_TRACER, Tracer, cost_usd
from ..types import Request

SEVERITIES = ("blocker", "major", "minor", "nit")

CRITIQUE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "severity": {"type": "string", "enum": list(SEVERITIES)},
                    "location": {"type": "string"},
                    "problem": {"type": "string"},
                    "why_it_matters": {"type": "string"},
                    "fix": {"type": "string"},
                },
                "required": ["severity", "location", "problem", "why_it_matters", "fix"],
                "additionalProperties": False,
            },
        },
        "verdict": {"type": "string", "enum": ["ship", "revise"]},
    },
    "required": ["findings", "verdict"],
    "additionalProperties": False,
}

CRITIC_SYSTEM = """\
You are a reviewer. You do not rewrite the work; you find what is wrong with it.

Rules that make a review useful:
- A finding must name a concrete failure: an input that breaks it, a case it \
does not handle, an assumption it makes that the task does not license. \
"Could be clearer" is not a finding.
- Severity is honest. `blocker` means it is wrong or unsafe. `nit` means you \
would mention it and still approve. Do not inflate to look thorough.
- If the work is correct, return an empty findings list and verdict "ship". \
Finding nothing is a valid and frequently correct review outcome.
- Never restate the work back. Never praise it.

Return JSON only.\
"""

REVISE_SYSTEM = """\
You are revising your own work in response to a review.

- Fix every blocker and major finding.
- For a finding you disagree with, keep your version and add one line at the \
end starting `DISAGREE:` naming the finding and why. Do not silently ignore it.
- Change nothing the review did not raise. Unprompted rewrites are how a \
revision loop makes things worse.
- Output the complete revised work only. No commentary, no fences.\
"""


@dataclass(slots=True)
class Finding:
    severity: str
    location: str
    problem: str
    why_it_matters: str
    fix: str

    @property
    def rank(self) -> int:
        return SEVERITIES.index(self.severity) if self.severity in SEVERITIES else len(SEVERITIES)


@dataclass(slots=True)
class CritiqueRound:
    index: int
    findings: list[Finding]
    verdict: str
    revised: str | None = None

    @property
    def actionable(self) -> list[Finding]:
        return [f for f in self.findings if f.severity in ("blocker", "major")]


@dataclass(slots=True)
class RefineResult:
    final: str
    draft: str
    rounds: list[CritiqueRound] = field(default_factory=list)
    cost_usd: float = 0.0
    stopped_because: str = ""

    @property
    def changed(self) -> bool:
        return self.final.strip() != self.draft.strip()

    @property
    def all_findings(self) -> list[Finding]:
        return [f for r in self.rounds for f in r.findings]


def refine(
    provider: Any,
    task: str,
    draft: str | None = None,
    *,
    rubric: str = "",
    rounds: int = 2,
    min_severity: str = "major",
    model: str = "claude-opus-5",
    critic_model: str | None = None,
    max_tokens: int = 16000,
    tracer: Tracer | None = None,
) -> RefineResult:
    """Draft, critique against a rubric, revise. Bounded and self-terminating.

    Pass ``draft`` to critique work that already exists -- that is the common
    case in a codebase, where the draft is a diff a human or another tool wrote.

    ``min_severity`` sets the bar for continuing: with the default, a round that
    produces only minors and nits ends the loop rather than triggering a
    revision that risks regressing working code.
    """
    tracer = tracer or NULL_TRACER
    cost = 0.0
    bar = SEVERITIES.index(min_severity)

    if draft is None:
        with tracer.span("critic.draft", kind="stage"):
            response = provider.complete(
                Request(
                    messages=[{"role": "user", "content": task}],
                    model=model,
                    max_tokens=max_tokens,
                    effort="high",
                    metadata={"lab": "critic", "stage": "draft"},
                )
            )
            draft = response.text
            cost += cost_usd(response.model or model, response.usage)

    current = draft
    seen: set[str] = {_fingerprint(current)}
    history: list[CritiqueRound] = []
    stopped = "rounds exhausted"

    for index in range(1, rounds + 1):
        with tracer.span("critic.critique", kind="stage", round=index):
            critique = provider.complete(
                Request(
                    messages=[
                        {
                            "role": "user",
                            "content": (
                                f"<task>\n{task}\n</task>\n\n"
                                + (f"<rubric>\n{rubric}\n</rubric>\n\n" if rubric else "")
                                + f"<work>\n{current}\n</work>"
                            ),
                        }
                    ],
                    model=critic_model or model,
                    system=CRITIC_SYSTEM,
                    max_tokens=8000,
                    output_schema=CRITIQUE_SCHEMA,
                    effort="high",
                    metadata={"lab": "critic", "stage": "critique", "round": index},
                )
            )
            cost += cost_usd(critique.model or model, critique.usage)

        try:
            parsed = json.loads(critique.text)
            findings = [Finding(**f) for f in parsed.get("findings", [])]
            verdict = parsed.get("verdict", "ship")
        except (ValueError, TypeError) as exc:
            history.append(CritiqueRound(index, [], "ship"))
            stopped = f"critic returned unusable output: {exc}"
            break

        round_record = CritiqueRound(index, findings, verdict)
        history.append(round_record)

        worth_fixing = [f for f in findings if f.rank <= bar]
        if not worth_fixing:
            stopped = f"round {index} found nothing at or above '{min_severity}'"
            break

        with tracer.span("critic.revise", kind="stage", round=index):
            revision = provider.complete(
                Request(
                    messages=[
                        {
                            "role": "user",
                            "content": (
                                f"<task>\n{task}\n</task>\n\n"
                                f"<work>\n{current}\n</work>\n\n"
                                "<review>\n"
                                + "\n".join(
                                    f"[{f.severity}] {f.location}: {f.problem}\n"
                                    f"  why: {f.why_it_matters}\n  fix: {f.fix}"
                                    for f in worth_fixing
                                )
                                + "\n</review>"
                            ),
                        }
                    ],
                    model=model,
                    system=REVISE_SYSTEM,
                    max_tokens=max_tokens,
                    effort="high",
                    metadata={"lab": "critic", "stage": "revise", "round": index},
                )
            )
            cost += cost_usd(revision.model or model, revision.usage)

        candidate = revision.text.strip()
        fingerprint = _fingerprint(candidate)
        if fingerprint in seen:
            # Reverted to something we have already produced: the loop is
            # oscillating between two states and further rounds only cost money.
            stopped = f"oscillation detected at round {index}"
            break
        seen.add(fingerprint)
        round_record.revised = candidate
        current = candidate

    return RefineResult(
        final=current,
        draft=draft,
        rounds=history,
        cost_usd=cost,
        stopped_because=stopped,
    )


def _fingerprint(text: str) -> str:
    return hashlib.sha256(" ".join(text.split()).encode()).hexdigest()[:16]
