"""A small offline evaluation harness for a model alias.

Each case is a prompt plus simple, deterministic checks on the reply. That cannot
judge quality, but it catches regressions: a custom model that stops following its
system prompt, rambles, or leaks something it should not. Model calls go through
the gateway, so any alias works, local or hosted.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from ..errors import ConfigError
from ..gateway import AIGateway

MAX_PATTERN_CHARS = 200
MAX_CASES = 500


@dataclass(frozen=True)
class EvalCase:
    id: str
    prompt: str
    contains: tuple[str, ...] = ()
    not_contains: tuple[str, ...] = ()
    regex: str | None = None
    max_chars: int | None = None


@dataclass
class CaseResult:
    case_id: str
    passed: bool
    failures: list[str] = field(default_factory=list)
    reply: str = ""


@dataclass
class EvalReport:
    model: str
    results: list[CaseResult] = field(default_factory=list)

    @property
    def passed(self) -> int:
        return sum(1 for r in self.results if r.passed)

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def pass_rate(self) -> float:
        return self.passed / self.total if self.total else 0.0


def load_cases(path: str | Path) -> list[EvalCase]:
    file = Path(path)
    try:
        data: Any = yaml.safe_load(file.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"cannot read {file}: {exc}") from exc
    raw_cases = (data or {}).get("cases") if isinstance(data, dict) else None
    if not isinstance(raw_cases, list) or not raw_cases:
        raise ConfigError(f"{file} needs a non-empty `cases:` list")
    if len(raw_cases) > MAX_CASES:
        raise ConfigError(f"{file} has more than {MAX_CASES} cases")

    cases: list[EvalCase] = []
    seen: set[str] = set()
    for raw in raw_cases:
        try:
            case_id = str(raw["id"])
            prompt = str(raw["prompt"])
        except (KeyError, TypeError) as exc:
            raise ConfigError(f"each case needs `id` and `prompt`: {raw!r}") from exc
        if case_id in seen:
            raise ConfigError(f"duplicate case id {case_id!r}")
        seen.add(case_id)
        expect = raw.get("expect") or {}
        pattern = expect.get("regex")
        if pattern is not None:
            if len(str(pattern)) > MAX_PATTERN_CHARS:
                raise ConfigError(f"case {case_id!r}: regex longer than {MAX_PATTERN_CHARS}")
            try:
                re.compile(str(pattern))
            except re.error as exc:
                raise ConfigError(f"case {case_id!r}: bad regex: {exc}") from exc
        cases.append(
            EvalCase(
                id=case_id,
                prompt=prompt,
                contains=tuple(str(x) for x in expect.get("contains") or ()),
                not_contains=tuple(str(x) for x in expect.get("not_contains") or ()),
                regex=str(pattern) if pattern is not None else None,
                max_chars=int(expect["max_chars"]) if "max_chars" in expect else None,
            )
        )
    return cases


def check_reply(case: EvalCase, reply: str) -> list[str]:
    """Return the failed checks for one reply; empty means it passed."""
    failures: list[str] = []
    lowered = reply.lower()
    for needle in case.contains:
        if needle.lower() not in lowered:
            failures.append(f"missing {needle!r}")
    for needle in case.not_contains:
        if needle.lower() in lowered:
            failures.append(f"should not contain {needle!r}")
    if case.regex is not None and not re.search(case.regex, reply):
        failures.append(f"does not match /{case.regex}/")
    if case.max_chars is not None and len(reply) > case.max_chars:
        failures.append(f"longer than {case.max_chars} characters ({len(reply)})")
    return failures


async def run_eval(
    gateway: AIGateway, model: str, cases: list[EvalCase], *, limit: int | None = None
) -> EvalReport:
    """Run the cases in order against ``model`` and score each reply."""
    report = EvalReport(model=model)
    for case in cases[:limit] if limit else cases:
        reply = await gateway.complete(model, case.prompt, max_tokens=512, temperature=0.0)
        failures = check_reply(case, reply.text)
        report.results.append(CaseResult(case.id, not failures, failures, reply.text))
    return report
