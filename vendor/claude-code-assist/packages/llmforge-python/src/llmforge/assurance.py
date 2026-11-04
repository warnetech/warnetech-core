"""Assurance: the four coding principles, compiled into gates that can fail.

The Karpathy guidelines (see ``THIRD_PARTY_NOTICES.md``) are four rules that
demonstrably reduce LLM coding mistakes:

    1. Think Before Coding    -- don't assume; surface confusion and tradeoffs
    2. Simplicity First       -- minimum code that solves the problem
    3. Surgical Changes       -- touch only what the request requires
    4. Goal-Driven Execution  -- define success criteria; loop until verified

They ship as prose in a ``CLAUDE.md``, and as prose they work the way all prompt
guidance works: most of the time, and silently not the rest of the time. You
cannot tell from a diff whether the agent followed rule 3 or merely read it.

Where a mistake is cheap, that is a fine trade. Where a mistake is expensive to
reverse -- anything touching production, anything a person depends on -- advice
that cannot fail is not a control. So this module turns each principle into a
check with a verdict:

    ============================  ========================================
    principle                     gate
    ============================  ========================================
    Think Before Coding           ``AssumptionLedger``  -- non-trivial work
                                  requires stated assumptions and named
                                  open questions before code is generated.
    Simplicity First              ``complexity_budget`` -- measures what was
                                  added and flags speculative generality.
    Surgical Changes              ``diff_discipline``   -- every hunk must
                                  trace to the request; drive-by edits and
                                  deleted comments are findings.
    Goal-Driven Execution         ``SuccessCriteria``   -- a run cannot start
                                  without a criterion and a verifier.
    ============================  ========================================

None of these need a model. They are static analysis over text you already
have, which means they are fast, free, deterministic, and cannot themselves
hallucinate -- the properties you want in the thing that says "no".

Use :class:`Preflight` and :class:`Postflight` to run them as a pair around a
change, or wire :func:`gate_agent` to refuse a run that has no success criteria.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

Severity = Literal["blocker", "warn", "note"]


@dataclass(slots=True)
class Violation:
    """One failed check, with enough detail to act on without re-deriving it.

    ``code`` is the stable, machine-readable identity of the finding. Prose
    messages get reworded; codes do not, which is what lets the parity
    fixtures pin the Python and TypeScript gates to the same behaviour.
    """

    principle: str
    severity: Severity
    message: str
    code: str = ""
    location: str = ""
    remedy: str = ""

    def render(self) -> str:
        where = f" ({self.location})" if self.location else ""
        line = f"[{self.severity:>7}] {self.principle}{where}: {self.message}"
        return f"{line}\n          fix: {self.remedy}" if self.remedy else line


@dataclass(slots=True)
class Verdict:
    """The outcome of a gate. Falsy when anything blocking was found."""

    violations: list[Violation] = field(default_factory=list)
    checked: list[str] = field(default_factory=list)

    @property
    def blockers(self) -> list[Violation]:
        return [v for v in self.violations if v.severity == "blocker"]

    @property
    def passed(self) -> bool:
        return not self.blockers

    def __bool__(self) -> bool:
        return self.passed

    def __add__(self, other: Verdict) -> Verdict:
        return Verdict(self.violations + other.violations, self.checked + other.checked)

    def raise_if_blocked(self) -> None:
        """The fail-safe call. Use it where proceeding is worse than stopping."""
        if self.blockers:
            raise AssuranceError(self)

    def report(self) -> str:
        if not self.violations:
            return f"assurance: pass ({len(self.checked)} checks)"
        head = (
            f"assurance: {len(self.blockers)} blocking, "
            f"{len(self.violations) - len(self.blockers)} advisory "
            f"({len(self.checked)} checks)"
        )
        return "\n".join([head, *(v.render() for v in self.violations)])


class AssuranceError(RuntimeError):
    """Raised by :meth:`Verdict.raise_if_blocked`. Carries the full verdict."""

    def __init__(self, verdict: Verdict):
        super().__init__(verdict.report())
        self.verdict = verdict


# --------------------------------------------------------------------------- #
# 1. Think Before Coding
# --------------------------------------------------------------------------- #

# Phrases that mark an unexamined assumption presented as fact. Weak signals
# individually; the gate only fires when nothing was declared at all.
HEDGE_PATTERNS = (
    re.compile(r"(?i)\b(?:presumably|I assume|assuming|probably|should be|I'll just|"
               r"it seems|likely means|I'll go with)\b"),
)


@dataclass(slots=True)
class AssumptionLedger:
    """What the agent believes but was not told. Principle 1, made auditable.

    The ledger exists so that a wrong assumption is *visible before* it becomes
    a wrong implementation. An empty ledger on a non-trivial task is itself the
    finding: no real request is fully specified, so an agent that declared no
    assumptions did not look for them.

    >>> ledger = AssumptionLedger(task="add retry to the uploader")
    >>> _ = ledger.assume("retries are safe because uploads are idempotent",
    ...                   because="upload() PUTs to a content-addressed key")
    >>> ledger.check().passed
    True
    """

    task: str = ""
    assumptions: list[tuple[str, str]] = field(default_factory=list)
    open_questions: list[str] = field(default_factory=list)
    rejected_alternatives: list[tuple[str, str]] = field(default_factory=list)
    trivial: bool = False
    """Set for genuine one-liners. The guidelines exempt them, and so does this."""

    def assume(self, claim: str, because: str = "") -> AssumptionLedger:
        self.assumptions.append((claim, because))
        return self

    def ask(self, question: str) -> AssumptionLedger:
        self.open_questions.append(question)
        return self

    def rejected(self, option: str, why: str) -> AssumptionLedger:
        self.rejected_alternatives.append((option, why))
        return self

    def check(self, *, blocking_questions: bool = True) -> Verdict:
        """Verify the ledger is complete enough to proceed on."""
        verdict = Verdict(checked=["think-before-coding"])
        if self.trivial:
            return verdict

        if not self.assumptions:
            verdict.violations.append(
                Violation(
                    "Think Before Coding",
                    "blocker",
                    "no assumptions declared for a non-trivial task",
                    code="think.no-assumptions",
                    remedy="state what you inferred that the request did not say, "
                    "or mark the task trivial=True if it genuinely is",
                )
            )

        for claim, because in self.assumptions:
            if not because:
                verdict.violations.append(
                    Violation(
                        "Think Before Coding",
                        "warn",
                        f"assumption has no evidence: {claim!r}",
                    code="think.unsupported-assumption",
                        remedy="cite what in the codebase or request supports it, "
                        "or move it to open_questions",
                    )
                )

        if blocking_questions and self.open_questions:
            verdict.violations.append(
                Violation(
                    "Think Before Coding",
                    "blocker",
                    f"{len(self.open_questions)} unanswered question(s): "
                    + "; ".join(self.open_questions[:3]),
                    code="think.open-questions",
                    remedy="answer them, or proceed explicitly under a stated assumption",
                )
            )
        return verdict

    def render(self) -> str:
        """The ledger as a prompt block or a PR comment."""
        lines = [f"## Assumptions for: {self.task}" if self.task else "## Assumptions"]
        lines += [f"- {c}" + (f" (because {b})" if b else " (UNSUPPORTED)")
                  for c, b in self.assumptions] or ["- (none declared)"]
        if self.rejected_alternatives:
            lines += ["", "## Considered and rejected"]
            lines += [f"- {opt}: {why}" for opt, why in self.rejected_alternatives]
        if self.open_questions:
            lines += ["", "## Open questions"]
            lines += [f"- {q}" for q in self.open_questions]
        return "\n".join(lines)


def scan_hedging(text: str) -> Verdict:
    """Flag unexamined assumptions phrased as fact in agent output.

    Advisory only. Hedge words are a *symptom*, not a defect, and blocking on
    them would train the agent to sound certain rather than to be certain --
    which is the opposite of what principle 1 is for.
    """
    verdict = Verdict(checked=["hedging"])
    for pattern in HEDGE_PATTERNS:
        for match in pattern.finditer(text):
            line_no = text[: match.start()].count("\n") + 1
            verdict.violations.append(
                Violation(
                    "Think Before Coding",
                    "note",
                    f"unexamined assumption phrased as fact: {match.group(0)!r}",
                    code="think.hedging",
                    location=f"line {line_no}",
                    remedy="promote it to the assumption ledger, or verify it",
                )
            )
    return verdict


# --------------------------------------------------------------------------- #
# 2. Simplicity First
# --------------------------------------------------------------------------- #

# Shapes that appear when a model builds for imagined future requirements.
#
# Removed: `unused-config-knob`, which claimed to flag "a configuration point
# added without a second caller". Whether a config point has a second caller is
# a cross-file fact, and a single-file regex cannot see callers -- so it never
# could do what it claimed. In practice it fired on every `model:` and
# `options:` in the repository (`model` contains `mode`; `options` contains
# `option`), which is the cry-wolf failure mode: a check with a bad
# false-positive rate gets ignored, and an ignored check is worse than no check
# because it still occupies the slot. Detecting this properly needs call-graph
# analysis, not a pattern table.
#
# Every pattern here matches BOTH Python and TypeScript/JavaScript syntax. A
# Python harness routinely reviews TypeScript diffs and vice versa; a gate that
# only recognises its own host language silently passes everything else, which
# is worse than not running -- it reports "clean" on code it never understood.
SPECULATIVE_PATTERNS: list[tuple[str, re.Pattern[str], str]] = [
    (
        "abstract-base",
        re.compile(
            r"^\s*(?:export\s+)?(?:abstract\s+)?class\s+\w*(?:Base|Abstract|Generic)\w*",
            re.M,
        ),
        "an abstraction introduced for a single implementation; write the "
        "concrete version and extract later if a second one arrives",
    ),
    (
        # Case-insensitive: `HandlerFactory` and `make_handler_factory` are the
        # same smell. `Provider` is deliberately absent -- it is legitimate
        # domain vocabulary often enough that flagging it trains people to
        # ignore this whole category.
        "factory",
        re.compile(
            r"^\s*(?:export\s+)?(?:async\s+)?(?:def|class|function|const|let)\s+"
            r"\w*(?:Factory|Builder|Manager)\w*",
            re.M | re.I,
        ),
        "an indirection layer; if there is exactly one thing being built, "
        "construct it directly",
    ),
    (
        "swallowed-error",
        re.compile(
            r"except\s+\w*(?:Exception|Error)?\s*(?:as\s+\w+\s*)?:\s*\n\s*pass"
            r"|catch\s*(?:\([^)]*\))?\s*\{\s*\}",
            re.M,
        ),
        "error handling for a scenario that is either impossible (delete it) "
        "or possible (handle it properly)",
    ),
    (
        "todo-scaffold",
        re.compile(r"(?:#|//)\s*(?:TODO|FIXME|for future use|not implemented yet)", re.I),
        "scaffolding for work that was not requested",
    ),
]


@dataclass(slots=True)
class ComplexityReport:
    added_lines: int
    definitions: int
    max_nesting: int
    findings: list[Violation] = field(default_factory=list)


def complexity_budget(
    code: str,
    *,
    max_lines: int | None = None,
    max_definitions: int | None = None,
    max_nesting: int = 4,
    baseline_lines: int | None = None,
    ratio: float = 4.0,
) -> Verdict:
    """Measure what a change added and flag speculative generality.

    Principle 2 says "if you write 200 lines and it could be 50, rewrite it".
    Nothing can decide *could be* mechanically -- but the signals that
    correlate with it are all measurable: raw size against a declared budget,
    size against the code it replaces, nesting depth, and the specific shapes
    that show up when a model builds for imagined future requirements.

    ``baseline_lines`` is the strongest signal available: a rewrite that is four
    times the size of what it replaces is nearly always doing more than it was
    asked to.

    >>> code = "def f(x):\\n    return x + 1\\n"
    >>> complexity_budget(code, max_lines=10).passed
    True
    >>> complexity_budget(code, max_lines=1).passed
    False
    """
    verdict = Verdict(checked=["simplicity-first"])
    # Both comment styles, in both languages. Counting `//` as code in Python
    # (or `#` in TypeScript) makes the same file measure differently depending
    # on which implementation ran -- pinned by fixtures/assurance-parity.json.
    lines = [
        ln
        for ln in code.splitlines()
        if ln.strip() and not ln.strip().startswith(("#", "//"))
    ]
    added = len(lines)

    if max_lines is not None and added > max_lines:
        verdict.violations.append(
            Violation(
                "Simplicity First",
                "blocker",
                f"{added} lines exceeds the declared budget of {max_lines}",
                    code="simplicity.line-budget",
                remedy="cut to the minimum that solves the stated problem, "
                "or raise the budget deliberately and say why",
            )
        )

    if baseline_lines and added > baseline_lines * ratio:
        verdict.violations.append(
            Violation(
                "Simplicity First",
                "blocker",
                f"{added} lines replaces {baseline_lines} "
                f"({added / baseline_lines:.1f}x growth)",
                code="simplicity.growth-ratio",
                remedy="a rewrite this much larger is usually solving problems "
                "nobody asked about; identify what is not required",
            )
        )

    definitions = len(re.findall(r"^\s*(?:async\s+)?(?:def|class)\s+\w+", code, re.M))
    if max_definitions is not None and definitions > max_definitions:
        verdict.violations.append(
            Violation(
                "Simplicity First",
                "warn",
                f"{definitions} definitions exceeds {max_definitions}",
                    code="simplicity.definition-count",
                remedy="collapse single-use helpers into their caller",
            )
        )

    nesting = _max_indent_depth(code)
    if nesting > max_nesting:
        verdict.violations.append(
            Violation(
                "Simplicity First",
                "warn",
                f"nesting depth {nesting} exceeds {max_nesting}",
                    code="simplicity.nesting-depth",
                remedy="invert conditions and return early",
            )
        )

    for name, pattern, remedy in SPECULATIVE_PATTERNS:
        for match in pattern.finditer(code):
            verdict.violations.append(
                Violation(
                    "Simplicity First",
                    "note",
                    f"possible speculative generality ({name}): {match.group(0).strip()[:60]!r}",
                    code="simplicity.speculative",
                    location=f"line {code[: match.start()].count(chr(10)) + 1}",
                    remedy=remedy,
                )
            )
    return verdict


# A line that opens a block, in either language: a Python compound statement
# ending in `:`, or a C-family line ending in `{`.
_BLOCK_OPENER = re.compile(
    r"^(?:async\s+)?(?:if|elif|else|for|while|with|try|except|finally|def|class|match|case)\b"
    r".*:\s*(?:#.*)?$"
    r"|\{\s*(?://.*)?$"
)


def _max_indent_depth(code: str, tab_width: int = 4) -> int:
    """Nesting depth, measured on lines that actually open a block.

    Raw indentation is a bad proxy. A wrapped call argument or a multi-line
    dict literal is indented four, eight, sixteen spaces without nesting
    anything -- measuring indentation alone reported depth 8 for a file whose
    real nesting is 3, which is a false positive that trains people to ignore
    the check.

    Counting only block openers (`if`/`for`/`def`/... in Python, a trailing
    `{` in C-family syntax) measures the thing the check is named after. The
    indent unit is inferred from those lines rather than assumed, so a file
    does not measure differently depending on which language's implementation
    ran -- see fixtures/assurance-parity.json.
    """
    openers: list[int] = []
    for line in code.splitlines():
        stripped = line.strip()
        if not stripped or not _BLOCK_OPENER.search(stripped):
            continue
        leading = len(line) - len(line.lstrip(" \t"))
        openers.append(len(line[:leading].replace("\t", " " * tab_width)))

    indents = [width for width in openers if width > 0]
    if not indents:
        return 0
    unit = min(indents)
    return max(width // unit for width in openers)


# --------------------------------------------------------------------------- #
# 3. Surgical Changes
# --------------------------------------------------------------------------- #

_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$")
_FILE = re.compile(r"^\+\+\+ b/(.+)$")

_COMMENT_LINE = re.compile(r"^\s*(?://|#|\*|/\*|<!--|--)\s*\S")
_WHITESPACE_ONLY = re.compile(r"^\s*$")


@dataclass(slots=True)
class Hunk:
    file: str
    header: str
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)

    @property
    def is_pure_formatting(self) -> bool:
        """Added and removed lines are identical once whitespace is normalized."""
        if not self.added or not self.removed:
            return False
        norm = lambda seq: [re.sub(r"\s+", " ", x).strip() for x in seq]  # noqa: E731
        return norm(self.added) == norm(self.removed)

    @property
    def removed_comments(self) -> list[str]:
        kept = {re.sub(r"\s+", " ", a).strip() for a in self.added}
        return [
            r for r in self.removed
            if _COMMENT_LINE.match(r) and re.sub(r"\s+", " ", r).strip() not in kept
        ]


def parse_diff(diff: str) -> list[Hunk]:
    """Parse a unified diff into hunks. Tolerant of git's extra headers."""
    hunks: list[Hunk] = []
    current: Hunk | None = None
    filename = "?"

    for line in diff.splitlines():
        file_match = _FILE.match(line)
        if file_match:
            filename = file_match.group(1)
            continue
        hunk_match = _HUNK.match(line)
        if hunk_match:
            current = Hunk(file=filename, header=line)
            hunks.append(current)
            continue
        if current is None:
            continue
        if line.startswith("+") and not line.startswith("+++"):
            current.added.append(line[1:])
        elif line.startswith("-") and not line.startswith("---"):
            current.removed.append(line[1:])
    return hunks


def diff_discipline(
    diff: str,
    *,
    request_terms: Iterable[str] = (),
    allowed_paths: Sequence[str] | None = None,
    allow_formatting: bool = False,
) -> Verdict:
    """Check that every hunk traces to the request. Principle 3, enforced.

    This is the gate with the most teeth, because "touch only what you must" is
    the principle an agent violates most often and a reviewer notices least --
    a drive-by rename buried in a 400-line diff reads as noise, gets skimmed,
    and lands.

    Three things it catches mechanically:

    * **Out-of-scope files.** A hunk in a path the request never mentioned.
    * **Pure reformatting.** Added and removed lines identical modulo
      whitespace: churn that costs review attention and buys nothing.
    * **Deleted comments.** The specific failure the guidelines call out --
      removing an explanation the agent did not understand. A comment is often
      the only record of *why*, and deleting it destroys information that the
      code itself does not carry.

    ``request_terms`` is what makes traceability checkable at all: pass the
    nouns from the actual request and a hunk touching an unrelated file is
    detectable rather than a judgment call.

    >>> diff = '''+++ b/src/auth.py
    ... @@ -1,2 +1,2 @@
    ... -# validated against RFC 7519 section 4.1.4
    ... +
    ... '''
    >>> [v.message for v in diff_discipline(diff).violations][0][:24]
    'comment removed without '
    """
    verdict = Verdict(checked=["surgical-changes"])
    terms = {t.lower() for t in request_terms}
    hunks = parse_diff(diff)

    touched = sorted({h.file for h in hunks})
    for path in touched:
        if allowed_paths is not None and not any(path.startswith(p) for p in allowed_paths):
            verdict.violations.append(
                Violation(
                    "Surgical Changes",
                    "blocker",
                    f"edit outside the declared scope: {path}",
                    code="surgical.out-of-scope",
                    location=path,
                    remedy=f"the request authorized {list(allowed_paths)}; "
                    "raise the scope explicitly or revert this file",
                )
            )
        elif terms and not (terms & _path_terms(path)):
            verdict.violations.append(
                Violation(
                    "Surgical Changes",
                    "warn",
                    f"{path} does not obviously relate to the request",
                    code="surgical.unrelated-file",
                    location=path,
                    remedy="state which part of the request required this file, "
                    "or drop it from the change",
                )
            )

    for hunk in hunks:
        if hunk.is_pure_formatting and not allow_formatting:
            verdict.violations.append(
                Violation(
                    "Surgical Changes",
                    "warn",
                    "hunk is pure reformatting",
                    code="surgical.pure-formatting",
                    location=f"{hunk.file} {hunk.header.strip()}",
                    remedy="revert it; formatting churn costs review attention "
                    "and hides the real change",
                )
            )
        for comment in hunk.removed_comments:
            verdict.violations.append(
                Violation(
                    "Surgical Changes",
                    "blocker",
                    f"comment removed without replacement: {comment.strip()[:70]!r}",
                    code="surgical.comment-deleted",
                    location=hunk.file,
                    remedy="restore it, or say what you learned that makes it wrong; "
                    "a comment is often the only record of why the code is like this",
                )
            )
    return verdict


def _path_terms(path: str) -> set[str]:
    return {p.lower() for p in re.split(r"[/_.\-]", path) if len(p) > 2}


# --------------------------------------------------------------------------- #
# 4. Goal-Driven Execution
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class Criterion:
    """One verifiable success condition and the check that decides it."""

    description: str
    verify: Callable[[], tuple[bool, str]] | None = None

    def run(self) -> tuple[bool, str]:
        if self.verify is None:
            return False, "no verifier attached"
        try:
            return self.verify()
        except Exception as exc:  # noqa: BLE001 - a broken verifier is a failed check
            return False, f"verifier raised {type(exc).__name__}: {exc}"


# Phrasings that describe an activity rather than an outcome. A criterion you
# cannot fail is not a criterion.
VAGUE_CRITERIA = re.compile(
    r"(?i)^\s*(?:make it work|fix it|improve|clean up|handle errors|"
    r"add tests|refactor|optimi[sz]e|better|properly|correctly)\s*\.?\s*$"
)


@dataclass(slots=True)
class SuccessCriteria:
    """The goal of a run, stated so a machine can decide whether it was met.

    Principle 4 in one object. Its real job is refusal: :meth:`check` fails a
    run that has no criteria, and :meth:`verify` reports which criteria hold.
    "Strong success criteria let you loop independently" -- and the corollary
    is that a loop launched without them cannot know when to stop, so it stops
    when it runs out of budget or when it feels finished. Neither is a result.

    >>> goals = SuccessCriteria("add retry to uploader")
    >>> _ = goals.require("uploader_test.py passes", lambda: (True, "3 passed"))
    >>> goals.check().passed
    True
    >>> goals.verify().passed
    True
    """

    task: str = ""
    criteria: list[Criterion] = field(default_factory=list)
    plan: list[str] = field(default_factory=list)

    def require(
        self, description: str, verify: Callable[[], tuple[bool, str]] | None = None
    ) -> SuccessCriteria:
        self.criteria.append(Criterion(description, verify))
        return self

    def step(self, action: str, verify: str) -> SuccessCriteria:
        """Add a plan step in the guidelines' ``[step] -> verify: [check]`` form."""
        self.plan.append(f"{action} -> verify: {verify}")
        return self

    def check(self, *, require_verifiers: bool = True) -> Verdict:
        """Pre-flight: are these criteria good enough to launch a loop on?"""
        verdict = Verdict(checked=["goal-driven-execution"])
        if not self.criteria:
            verdict.violations.append(
                Violation(
                    "Goal-Driven Execution",
                    "blocker",
                    "no success criteria defined",
                    code="goal.no-criteria",
                    remedy="state what must be true when this is done, as something "
                    "that can be checked -- e.g. 'test_x passes', not 'it works'",
                )
            )
        for criterion in self.criteria:
            if VAGUE_CRITERIA.match(criterion.description):
                verdict.violations.append(
                    Violation(
                        "Goal-Driven Execution",
                        "blocker",
                        f"criterion is not verifiable: {criterion.description!r}",
                    code="goal.vague-criterion",
                        remedy="restate it as an observable outcome: "
                        "'fix the bug' -> 'the test reproducing it passes'",
                    )
                )
            if require_verifiers and criterion.verify is None:
                verdict.violations.append(
                    Violation(
                        "Goal-Driven Execution",
                        "warn",
                        f"criterion has no automated verifier: {criterion.description!r}",
                    code="goal.no-verifier",
                        remedy="attach a callable so the loop can decide for itself; "
                        "without one a human must adjudicate every iteration",
                    )
                )
        return verdict

    def verify(self) -> Verdict:
        """Post-flight: run every verifier and report what did not hold."""
        verdict = Verdict(checked=[f"criterion:{c.description}" for c in self.criteria])
        for criterion in self.criteria:
            ok, detail = criterion.run()
            if not ok:
                verdict.violations.append(
                    Violation(
                        "Goal-Driven Execution",
                        "blocker",
                        f"unmet: {criterion.description}",
                    code="goal.unmet",
                        remedy=detail,
                    )
                )
        return verdict

    def render(self) -> str:
        lines = [f"## Goal: {self.task}" if self.task else "## Goal"]
        lines += ["", "Done when:"]
        lines += [f"{i + 1}. {c.description}" for i, c in enumerate(self.criteria)] or ["  (none)"]
        if self.plan:
            lines += ["", "Plan:"]
            lines += [f"{i + 1}. {s}" for i, s in enumerate(self.plan)]
        return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Composition
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class Preflight:
    """Everything checked *before* code is generated.

    Runs at the cheapest possible moment: an assumption caught here costs one
    conversation turn, the same assumption caught in review costs a day, and
    caught in production it costs whatever it costs.
    """

    ledger: AssumptionLedger | None = None
    goals: SuccessCriteria | None = None

    def run(self) -> Verdict:
        verdict = Verdict()
        if self.ledger is not None:
            verdict = verdict + self.ledger.check()
        if self.goals is not None:
            verdict = verdict + self.goals.check()
        if self.ledger is None and self.goals is None:
            verdict.violations.append(
                Violation(
                    "Assurance",
                    "blocker",
                    "preflight ran with neither an assumption ledger nor success criteria",
                    code="assurance.no-preflight-input",
                    remedy="an unbounded task with no stated goal cannot be verified; "
                    "supply at least one",
                )
            )
        return verdict


@dataclass(slots=True)
class Postflight:
    """Everything checked *after* a change exists, before it is accepted."""

    diff: str = ""
    code: str = ""
    request_terms: Sequence[str] = ()
    allowed_paths: Sequence[str] | None = None
    goals: SuccessCriteria | None = None
    max_lines: int | None = None
    baseline_lines: int | None = None

    def run(self) -> Verdict:
        verdict = Verdict()
        if self.diff:
            verdict = verdict + diff_discipline(
                self.diff,
                request_terms=self.request_terms,
                allowed_paths=self.allowed_paths,
            )
        if self.code:
            verdict = verdict + complexity_budget(
                self.code, max_lines=self.max_lines, baseline_lines=self.baseline_lines
            )
        if self.goals is not None:
            verdict = verdict + self.goals.verify()
        return verdict


def gate_agent(agent: Any, goals: SuccessCriteria, *, ledger: AssumptionLedger | None = None) -> Any:
    """Refuse to launch an agent that has no verifiable goal.

    Wraps ``agent.run`` so the pre-flight verdict is enforced rather than
    advisory. This is the fail-safe posture: the default is *do not start*, and
    starting requires you to have said what done means.

    >>> from llmforge import Agent, FakeProvider
    >>> goals = SuccessCriteria("demo").require("output mentions ok", lambda: (True, ""))
    >>> gated = gate_agent(Agent(FakeProvider(["ok"])), goals)
    >>> gated.run("go").text
    'ok'
    """
    preflight = Preflight(ledger=ledger, goals=goals).run()
    inner_run = agent.run

    def run(*args: Any, **kwargs: Any) -> Any:
        preflight.raise_if_blocked()
        return inner_run(*args, **kwargs)

    agent.run = run  # type: ignore[method-assign]
    agent.preflight = preflight  # type: ignore[attr-defined]
    agent.goals = goals  # type: ignore[attr-defined]
    return agent
