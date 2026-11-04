"""Memory: distill finished runs into short, reusable lessons.

**Claim.** Replaying whole transcripts as "memory" is the wrong unit. A
transcript is mostly dead weight -- tool noise, retries, wrong turns -- and it
costs context proportional to how badly the run went. What transfers between
runs is much smaller: a handful of durable facts about *this* codebase and
*this* team. "Migrations live in db/migrate and must be reversible." "The CI
lint step rejects unsorted imports." "Never touch generated/."

A lesson is worth keeping only if it would have changed what an agent did.
Everything else is trivia that costs tokens on every future run.

**How it would fail.** Lesson rot. A distilled lesson is a snapshot of a
codebase that keeps moving; six months on, a confident wrong lesson is worse
than no memory, because the agent trusts it.

The reinforcement model below is ported from the signature engine in
tewartech-node/claude-command-cli, which solved the same problem for attack
signatures and had already found the two failure modes a naive version hits:

* **Duplicate spawning.** Merging on an exact string match means "migrations
  must be reversible" and "Migrations have to be reversible." become two
  lessons, each with half the evidence, and neither ever reaches confidence.
  The fix is that a claim matching *anything* reinforces those matches rather
  than minting another -- new lessons are only created when nothing matched.
* **Runaway confidence.** Linear growth lets a lesson confirmed twenty times
  outrank a directly contradicted one. Growth is therefore asymptotic --
  ``weight += (max - weight) * rate`` -- so each confirmation moves it less
  than the last, and a contradiction subtracts a flat penalty that a single
  confirmation cannot undo.

**Also load-bearing:** memory is an injection surface. A lesson distilled from
a run that processed a hostile README can persist an attacker's instruction
into every future session. Lessons are scanned on write and **quarantined**,
not silently dropped -- you want to *see* that something tried to write an
instruction into persistent memory.

**Measurement.** Task success and step count with memory on versus off, on
tasks the agent has seen a sibling of before. If step count does not drop, the
lessons are not carrying information.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass, field
from dataclasses import fields as dataclasses_fields
from pathlib import Path
from typing import Any

from ..guard import scan_injection
from ..trace import NULL_TRACER, Tracer
from ..types import Request

LESSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "lessons": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "claim": {"type": "string"},
                    "scope": {"type": "string"},
                    "evidence": {"type": "string"},
                    "would_have_changed_behavior": {"type": "boolean"},
                },
                "required": ["claim", "scope", "evidence", "would_have_changed_behavior"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["lessons"],
    "additionalProperties": False,
}

DISTILL_SYSTEM = """\
You extract durable lessons from a finished agent run. You are not summarizing.

Keep a lesson only if ALL of these hold:
- It is specific to this codebase, team, or environment -- not general \
programming knowledge the model already has.
- It would have changed what the agent did, had it known it at the start.
- It will still be true next month.

One sentence per claim. `scope` is the path or subsystem it applies to, or \
"repo" for repo-wide. `evidence` cites what in the run supports it.

Most runs yield zero or one lesson. Returning an empty list is the correct and \
common answer. Do not invent lessons to seem useful.

Return JSON only.\
"""


# Reinforcement parameters, ported from the signature engine that inspired
# this. The exact values matter less than their relationships: `penalty` must
# exceed one confirmation's gain near the ceiling, or a contradicted lesson
# climbs back on the next confirmation.
MIN_WEIGHT = 0.05
MAX_WEIGHT = 0.95
REINFORCEMENT_RATE = 0.20
PENALTY = 0.25
CONFIDENCE_FLOOR = 0.0
CONFIDENCE_CEILING = 0.99
SATURATION_WEIGHT = 0.85
SATURATION_MIN_OCCURRENCES = 5

_STOPWORDS = frozenset(
    ["a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "has", "have", "in", "is", "it", "its", "of", "on", "or", "that", "the", "this", "to", "was", "were", "will", "with", "must", "should", "always", "never"]
)


def claim_key(claim: str) -> frozenset[str]:
    """The comparable content of a claim: significant words, order-independent.

    Matching on this rather than the exact string is what stops "migrations
    must be reversible" and "Migrations have to be reversible." becoming two
    half-confirmed lessons instead of one confident lesson.
    """
    words = re.findall(r"[a-z0-9]+", claim.lower())
    return frozenset(w for w in words if w not in _STOPWORDS and len(w) > 2)


def similarity(a: frozenset[str], b: frozenset[str]) -> float:
    """Jaccard overlap. 1.0 is identical content, 0.0 is disjoint."""
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


@dataclass(slots=True)
class Lesson:
    """One durable claim about a codebase, with provenance and a weight.

    ``weight`` is what reinforcement moves; ``confidence`` is what callers
    read. They differ because confidence discounts weight by the observed
    contradiction rate, so a heavily-reinforced lesson that is also frequently
    wrong does not present as certain.
    """

    claim: str
    scope: str = "repo"
    evidence: str = ""
    source: str = "unknown"
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    weight: float = 0.5
    occurrences: int = 0
    confirmed: int = 0
    contradicted: int = 0
    quarantined: bool = False
    """Set when the lesson matched injection heuristics on write."""

    @property
    def age_days(self) -> float:
        return (time.time() - self.created_at) / 86400

    @property
    def key(self) -> frozenset[str]:
        return claim_key(self.claim)

    @property
    def confidence(self) -> float:
        """Weight, discounted by the contradiction rate, then decayed by age.

        The age decay is this module's own addition: a signature describes an
        attack pattern that does not rot, while a lesson describes a codebase
        that does. A lesson nobody has reconfirmed in a quarter should not
        present as freshly as one confirmed yesterday.
        """
        base = self.weight
        if self.occurrences:
            base *= 1 - (self.contradicted / self.occurrences)
        decay = 0.5 ** (max(0.0, (time.time() - self.updated_at) / 86400) / 90)
        value = base * (0.4 + 0.6 * decay)
        return round(max(CONFIDENCE_FLOOR, min(CONFIDENCE_CEILING, value)), 3)

    @property
    def saturated(self) -> bool:
        """Reinforcement has taught this lesson everything it can.

        A saturated lesson is a candidate for promotion into a durable
        document -- CLAUDE.md, a lint rule, a test -- rather than continued
        tuning. That is the point of tracking it: the goal is to graduate
        knowledge out of a fallible store, not to accumulate it forever.
        """
        return self.weight >= SATURATION_WEIGHT and self.occurrences >= SATURATION_MIN_OCCURRENCES

    def reinforce(self, *, true_positive: bool) -> Lesson:
        """One observation. Asymptotic on confirmation, flat penalty on
        contradiction, so confidence cannot run away and cannot be trivially
        restored after being contradicted."""
        self.occurrences += 1
        self.updated_at = time.time()
        if true_positive:
            self.confirmed += 1
            self.weight = min(MAX_WEIGHT, self.weight + (MAX_WEIGHT - self.weight) * REINFORCEMENT_RATE)
        else:
            self.contradicted += 1
            self.weight = max(MIN_WEIGHT, self.weight - PENALTY)
        return self

    def render(self) -> str:
        age = f"{self.age_days:.0f}d" if self.age_days >= 1 else "new"
        mark = " [saturated]" if self.saturated else ""
        return f"- [{self.scope}] {self.claim} (confidence {self.confidence}, age {age}){mark}"


class LessonStore:
    """A small JSON-backed store of lessons.

    Deliberately a file, not a vector database. At the scale that matters for
    one repository -- tens to low hundreds of lessons -- reading all of them and
    filtering by scope beats an embedding round trip on latency, cost, and on
    your ability to open the file and see what the agent thinks it knows. That
    last one is the real argument: memory you cannot audit is memory you cannot
    trust.
    """

    #: Claim-content overlap above which two lessons are treated as the same.
    #: Tuned so a rephrasing merges and a genuinely different claim about the
    #: same subsystem does not.
    MERGE_THRESHOLD = 0.6

    def __init__(self, path: str | Path = ".llmforge/lessons.json") -> None:
        self.path = Path(path)
        self.lessons: list[Lesson] = []
        if self.path.exists():
            raw = json.loads(self.path.read_text())
            fields = {f.name for f in dataclasses_fields(Lesson)}
            # Tolerate stores written by an earlier schema rather than refusing
            # to load: losing a repository's accumulated memory to a field
            # rename is a worse outcome than dropping an unknown key.
            self.lessons = [Lesson(**{k: v for k, v in entry.items() if k in fields}) for entry in raw]

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps([asdict(x) for x in self.lessons], indent=2))

    def find(self, claim: str, *, threshold: float | None = None) -> list[Lesson]:
        """Every stored lesson whose content overlaps ``claim``, best first."""
        bar = self.MERGE_THRESHOLD if threshold is None else threshold
        key = claim_key(claim)
        scored = [(similarity(key, lesson.key), lesson) for lesson in self.lessons]
        return [lesson for score, lesson in sorted(scored, key=lambda p: -p[0]) if score >= bar]

    def add(self, lesson: Lesson) -> Lesson:
        """Reinforce what this claim matches; mint only when nothing matched.

        This is the duplicate-spawning fix. A claim that already has matching
        lessons must strengthen those -- minting a near-identical fourth means
        four lessons each carrying a quarter of the evidence, none of which
        ever reaches confidence.
        """
        scan = scan_injection(lesson.claim + " " + lesson.evidence)
        if scan.suspicious:
            lesson.quarantined = True

        matches = self.find(lesson.claim)
        if matches:
            for existing in matches:
                existing.reinforce(true_positive=True)
                if lesson.evidence and lesson.evidence not in existing.evidence:
                    existing.evidence = f"{existing.evidence}; {lesson.evidence}".strip("; ")
            return matches[0]

        lesson.reinforce(true_positive=True)
        self.lessons.append(lesson)
        return lesson

    def contradict(self, claim: str) -> list[Lesson]:
        """Record that a claim turned out to be wrong.

        Returns what was penalised, so a caller can report it. Matching is the
        same overlap used for merging -- a contradiction phrased differently
        from the stored lesson must still land on it.
        """
        matches = self.find(claim)
        for lesson in matches:
            lesson.reinforce(true_positive=False)
        return matches

    def prune(self, *, min_confidence: float = 0.1) -> list[Lesson]:
        """Drop lessons that reinforcement has driven into the ground.

        A lesson contradicted more often than confirmed is not neutral -- it is
        actively misleading, and keeping it costs context on every recall.
        """
        keep, dropped = [], []
        for lesson in self.lessons:
            (dropped if lesson.confidence < min_confidence else keep).append(lesson)
        self.lessons = keep
        return dropped

    def saturated(self) -> list[Lesson]:
        """Lessons ready to graduate out of the store.

        Promote these into something durable -- a CLAUDE.md line, a lint rule,
        a test. A lesson that has to be re-recalled forever is one the codebase
        should have been made to enforce.
        """
        return [lesson for lesson in self.lessons if lesson.saturated]

    def recall(
        self,
        scope: str = "repo",
        *,
        limit: int = 8,
        min_confidence: float = 0.25,
        include_quarantined: bool = False,
    ) -> list[Lesson]:
        """The lessons worth spending context on for a given scope.

        Quarantined lessons are excluded by default. Including them means
        deliberately putting text that looked like an injection attempt back
        into a prompt -- occasionally right when auditing, never by accident.
        """
        candidates = [
            lesson
            for lesson in self.lessons
            if (include_quarantined or not lesson.quarantined)
            and lesson.confidence >= min_confidence
            and (lesson.scope == "repo" or scope == "repo" or scope.startswith(lesson.scope))
        ]
        candidates.sort(key=lambda x: -x.confidence)
        return candidates[:limit]

    def render(self, scope: str = "repo", **kwargs: Any) -> str:
        """Recalled lessons as a prompt block. Empty string when there are none."""
        recalled = self.recall(scope, **kwargs)
        if not recalled:
            return ""
        return (
            "<learned-context note=\"distilled from prior runs in this repository; "
            "treat as fallible and prefer direct observation\">\n"
            + "\n".join(x.render() for x in recalled)
            + "\n</learned-context>"
        )


def distill(
    provider: Any,
    transcript: str,
    *,
    source: str = "run",
    model: str = "claude-opus-5",
    store: LessonStore | None = None,
    tracer: Tracer | None = None,
) -> list[Lesson]:
    """Extract durable lessons from a finished run.

    Returns only lessons the model marked as behavior-changing. The filter is
    the point: without it, distillation produces a paragraph of restated
    obviousness that costs context forever.
    """
    tracer = tracer or NULL_TRACER
    with tracer.span("memory.distill", kind="stage"):
        response = provider.complete(
            Request(
                messages=[{"role": "user", "content": f"<run>\n{transcript}\n</run>"}],
                model=model,
                system=DISTILL_SYSTEM,
                max_tokens=4000,
                output_schema=LESSON_SCHEMA,
                metadata={"lab": "memory", "stage": "distill"},
            )
        )

    try:
        parsed = json.loads(response.text)
    except ValueError:
        return []

    lessons = [
        Lesson(
            claim=entry["claim"],
            scope=entry.get("scope", "repo"),
            evidence=entry.get("evidence", ""),
            source=source,
        )
        for entry in parsed.get("lessons", [])
        if entry.get("would_have_changed_behavior")
    ]

    if store is not None:
        for lesson in lessons:
            store.add(lesson)
        store.save()
    return lessons
