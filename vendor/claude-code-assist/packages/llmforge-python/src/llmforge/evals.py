"""An eval harness sized for a repository, not a research lab.

The single highest-leverage thing you can add to an LLM integration is a suite
that fails when the feature gets worse. Not a benchmark -- twenty cases drawn
from your own bug tracker beat a public benchmark every time, because they
encode what *your* users actually asked for.

Two design choices that matter:

* **Variance is first-class.** ``repeats`` runs each case N times and reports
  pass *rate*, not pass/fail. A prompt that passes 3 times in 5 is not a
  passing prompt, and a single-shot suite cannot tell you that.
* **Grading is composable.** Graders are plain callables returning a
  :class:`Grade`. ``all_of`` / ``any_of`` combine them, so "valid JSON AND
  mentions the error code AND under 200 words" is three small graders.
"""

from __future__ import annotations

import statistics
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from .guard import ValidationError, validate_json
from .trace import cost_usd
from .types import Request, Response, Usage


@dataclass(slots=True)
class Grade:
    """The outcome of one grader on one output."""

    passed: bool
    score: float = 0.0
    detail: str = ""

    @staticmethod
    def ok(detail: str = "", score: float = 1.0) -> Grade:
        return Grade(True, score, detail)

    @staticmethod
    def fail(detail: str, score: float = 0.0) -> Grade:
        return Grade(False, score, detail)


@dataclass(slots=True)
class Case:
    """One eval case: an input, and what a good answer looks like."""

    id: str
    input: str | list[dict[str, Any]]
    grader: Callable[[Response, Case], Grade]
    system: str | None = None
    expected: Any = None
    """Free-form. Graders read it; the harness only carries it."""
    tags: set[str] = field(default_factory=set)
    weight: float = 1.0


@dataclass(slots=True)
class CaseResult:
    case_id: str
    grades: list[Grade]
    outputs: list[str]
    usage: Usage
    cost_usd: float
    seconds: float
    error: str | None = None

    @property
    def pass_rate(self) -> float:
        if not self.grades:
            return 0.0
        return sum(1 for g in self.grades if g.passed) / len(self.grades)

    @property
    def mean_score(self) -> float:
        return statistics.fmean(g.score for g in self.grades) if self.grades else 0.0

    @property
    def flaky(self) -> bool:
        """Passed sometimes and failed sometimes: the most useful signal here."""
        return 0.0 < self.pass_rate < 1.0


@dataclass(slots=True)
class SuiteResult:
    name: str
    results: list[CaseResult]
    repeats: int
    model: str

    @property
    def pass_rate(self) -> float:
        if not self.results:
            return 0.0
        total = sum(r.pass_rate for r in self.results)
        return total / len(self.results)

    @property
    def cost_usd(self) -> float:
        return sum(r.cost_usd for r in self.results)

    @property
    def flaky_cases(self) -> list[CaseResult]:
        return [r for r in self.results if r.flaky]

    @property
    def failures(self) -> list[CaseResult]:
        return [r for r in self.results if r.pass_rate < 1.0]

    def report(self, *, verbose: bool = False) -> str:
        """A terminal-friendly summary. This is what goes in the CI log."""
        lines = [
            f"{self.name} -- {self.model} x{self.repeats}",
            f"  pass rate : {self.pass_rate:6.1%} over {len(self.results)} cases",
            f"  cost      : ${self.cost_usd:.4f}",
            f"  flaky     : {len(self.flaky_cases)}",
        ]
        for result in self.results:
            if result.pass_rate == 1.0 and not verbose:
                continue
            mark = "FLAKY" if result.flaky else ("ERROR" if result.error else "FAIL")
            if result.pass_rate == 1.0:
                mark = "pass"
            lines.append(f"  [{mark:>5}] {result.case_id}  {result.pass_rate:.0%}")
            detail = next((g.detail for g in result.grades if not g.passed), result.error or "")
            if detail:
                lines.append(f"          {detail[:160]}")
        return "\n".join(lines)

    def assert_at_least(self, threshold: float) -> None:
        """Raise unless the suite met ``threshold``. The CI gate.

        Gate on a rate, not on perfection: a suite that must be 100% green gets
        deleted the first Friday it goes red for a reason nobody has time to fix.
        """
        if self.pass_rate < threshold:
            raise AssertionError(
                f"{self.name}: pass rate {self.pass_rate:.1%} below threshold {threshold:.1%}\n"
                + self.report()
            )


class Suite:
    """A named collection of cases, run against a provider."""

    def __init__(self, name: str, cases: Iterable[Case] = ()) -> None:
        self.name = name
        self.cases: list[Case] = list(cases)

    def add(self, case: Case) -> Case:
        self.cases.append(case)
        return case

    def case(
        self,
        id: str,  # noqa: A002
        input: str,  # noqa: A002
        **kwargs: Any,
    ) -> Callable[[Callable[[Response, Case], Grade]], Callable[[Response, Case], Grade]]:
        """Decorator form: attach a grader function to a new case.

        >>> suite = Suite("demo")
        >>> @suite.case("greets", "say hi")
        ... def _(response, case):
        ...     return Grade.ok() if "hi" in response.text.lower() else Grade.fail("no hi")
        >>> len(suite.cases)
        1
        """

        def register(fn: Callable[[Response, Case], Grade]) -> Callable[[Response, Case], Grade]:
            self.add(Case(id=id, input=input, grader=fn, **kwargs))
            return fn

        return register

    def run(
        self,
        provider: Any,
        *,
        model: str = "claude-opus-5",
        repeats: int = 1,
        system: str | None = None,
        max_tokens: int = 4000,
        effort: str | None = None,
        only_tags: set[str] | None = None,
        **request_kwargs: Any,
    ) -> SuiteResult:
        """Run every case ``repeats`` times and grade the outputs."""
        results: list[CaseResult] = []
        cases = [c for c in self.cases if not only_tags or (c.tags & only_tags)]

        for case in cases:
            grades: list[Grade] = []
            outputs: list[str] = []
            usage = Usage()
            cost = 0.0
            error: str | None = None
            started = time.time()

            for _ in range(repeats):
                request = Request(
                    messages=[{"role": "user", "content": case.input}],
                    model=model,
                    system=case.system or system,
                    max_tokens=max_tokens,
                    effort=effort,  # type: ignore[arg-type]
                    metadata={"eval_case": case.id},
                    **request_kwargs,
                )
                try:
                    response = provider.complete(request)
                except Exception as exc:  # noqa: BLE001 - one bad case must not kill the suite
                    error = f"{type(exc).__name__}: {exc}"
                    grades.append(Grade.fail(error))
                    continue

                usage = usage + response.usage
                cost += cost_usd(response.model or model, response.usage)
                outputs.append(response.text)
                try:
                    grades.append(case.grader(response, case))
                except Exception as exc:  # noqa: BLE001 - a broken grader is a failed case
                    grades.append(Grade.fail(f"grader raised {type(exc).__name__}: {exc}"))

            results.append(
                CaseResult(
                    case_id=case.id,
                    grades=grades,
                    outputs=outputs,
                    usage=usage,
                    cost_usd=cost,
                    seconds=time.time() - started,
                    error=error,
                )
            )

        return SuiteResult(name=self.name, results=results, repeats=repeats, model=model)


# --------------------------------------------------------------------------- #
# Graders
# --------------------------------------------------------------------------- #


def contains(*needles: str, case_sensitive: bool = False) -> Callable[[Response, Case], Grade]:
    """Passes when every needle appears in the output."""

    def grade(response: Response, _case: Case) -> Grade:
        haystack = response.text if case_sensitive else response.text.lower()
        missing = [
            n for n in needles if (n if case_sensitive else n.lower()) not in haystack
        ]
        if missing:
            return Grade.fail(f"missing: {missing}")
        return Grade.ok()

    return grade


def excludes(*needles: str) -> Callable[[Response, Case], Grade]:
    """Passes when none of the needles appear. Good for regression guards."""

    def grade(response: Response, _case: Case) -> Grade:
        lowered = response.text.lower()
        hits = [n for n in needles if n.lower() in lowered]
        return Grade.fail(f"forbidden content: {hits}") if hits else Grade.ok()

    return grade


def matches(pattern: str, flags: int = 0) -> Callable[[Response, Case], Grade]:
    """Passes when the output matches a regex."""
    import re

    compiled = re.compile(pattern, flags)

    def grade(response: Response, _case: Case) -> Grade:
        if compiled.search(response.text):
            return Grade.ok()
        return Grade.fail(f"no match for /{pattern}/")

    return grade


def json_valid(schema: dict[str, Any] | None = None) -> Callable[[Response, Case], Grade]:
    """Passes when the output parses as JSON and satisfies ``schema``."""

    def grade(response: Response, _case: Case) -> Grade:
        try:
            validate_json(response.text, schema)
        except ValidationError as exc:
            return Grade.fail(str(exc))
        return Grade.ok()

    return grade


def under_tokens(limit: int) -> Callable[[Response, Case], Grade]:
    """Passes when the output stayed under a length budget.

    Verbosity is a real regression: it costs money, it buries the answer, and it
    is the first thing that drifts when you edit a prompt.
    """

    def grade(response: Response, _case: Case) -> Grade:
        used = response.usage.output_tokens or max(1, len(response.text) // 4)
        if used <= limit:
            return Grade.ok(score=1.0 - used / (limit * 2))
        return Grade.fail(f"{used} output tokens exceeds limit {limit}")

    return grade


def all_of(*graders: Callable[[Response, Case], Grade]) -> Callable[[Response, Case], Grade]:
    """Conjunction. Reports every failure, not just the first."""

    def grade(response: Response, case: Case) -> Grade:
        grades = [g(response, case) for g in graders]
        failures = [g.detail for g in grades if not g.passed]
        if failures:
            return Grade.fail(" | ".join(failures))
        return Grade.ok(score=statistics.fmean(g.score for g in grades))

    return grade


def any_of(*graders: Callable[[Response, Case], Grade]) -> Callable[[Response, Case], Grade]:
    """Disjunction."""

    def grade(response: Response, case: Case) -> Grade:
        grades = [g(response, case) for g in graders]
        if any(g.passed for g in grades):
            return Grade.ok(score=max(g.score for g in grades))
        return Grade.fail(" & ".join(g.detail for g in grades))

    return grade


JUDGE_SYSTEM = (
    "You are a strict evaluator. You will be given a task, a candidate answer, "
    "and a rubric. Reply with JSON only: "
    '{"pass": boolean, "score": number between 0 and 1, "reason": "one sentence"}. '
    "Judge only against the rubric. Do not reward length, confidence, or formatting."
)


def llm_judge(
    provider: Any,
    rubric: str,
    *,
    model: str = "claude-opus-5",
    threshold: float = 0.7,
) -> Callable[[Response, Case], Grade]:
    """Grade with a model when the property is real but not mechanical.

    Use this for "is the explanation actually correct", not for "is it valid
    JSON" -- a deterministic grader is cheaper, faster and more trustworthy
    wherever one exists.

    Two rules keep judges honest: give the judge a *rubric*, not a vibe; and
    calibrate it against human labels on a handful of cases before you trust a
    number it produces. An uncalibrated judge measures the judge.
    """
    judge_schema = {
        "type": "object",
        "properties": {
            "pass": {"type": "boolean"},
            "score": {"type": "number"},
            "reason": {"type": "string"},
        },
        "required": ["pass", "score", "reason"],
        "additionalProperties": False,
    }

    def grade(response: Response, case: Case) -> Grade:
        prompt = (
            f"<task>\n{case.input}\n</task>\n\n"
            f"<rubric>\n{rubric}\n</rubric>\n\n"
            f"<candidate>\n{response.text}\n</candidate>"
        )
        verdict = provider.complete(
            Request(
                messages=[{"role": "user", "content": prompt}],
                model=model,
                system=JUDGE_SYSTEM,
                max_tokens=1000,
                output_schema=judge_schema,
                metadata={"role": "judge", "eval_case": case.id},
            )
        )
        try:
            data = validate_json(verdict.text, judge_schema)
        except ValidationError as exc:
            return Grade.fail(f"judge returned unusable output: {exc}")
        score = float(data["score"])
        passed = bool(data["pass"]) and score >= threshold
        return Grade(passed, score, f"judge: {data['reason']}")

    return grade


def sweep(
    suite: Suite,
    provider: Any,
    *,
    models: Sequence[str] = ("claude-opus-5",),
    efforts: Sequence[str | None] = (None,),
    repeats: int = 3,
    **kwargs: Any,
) -> list[SuiteResult]:
    """Run the suite across a grid of models and effort levels.

    This is how you answer "can we drop to a cheaper model here" with evidence
    instead of a hunch -- and how you discover that ``effort="medium"`` was
    already enough for two thirds of your traffic.
    """
    out: list[SuiteResult] = []
    for model in models:
        for effort in efforts:
            result = suite.run(provider, model=model, effort=effort, repeats=repeats, **kwargs)
            result.name = f"{suite.name}[{model}/{effort or 'default'}]"
            out.append(result)
    return out
