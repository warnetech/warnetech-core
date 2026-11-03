"""Parity: the Python and TypeScript gates must agree, case for case.

Two implementations of one contract drift. That is not a hypothetical -- the
project this discipline is borrowed from records the cost in its own source:

    "New encryption: extend warnetech_envelope -- never add a second
     implementation; that is what broke the CLI/Worker channel."

The assurance gates are exactly that shape: one contract, two implementations.
`fixtures/assurance-parity.json` is the pin. This module runs every case
through the Python gates; `packages/llmforge-typescript/test/parity.test.ts` runs the
identical file through the TypeScript ones. A finding added to one language
and not the other fails here.

Assertions are on the machine-readable `code`, never on prose. Messages get
reworded; codes do not.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from llmforge.assurance import (
    AssumptionLedger,
    SuccessCriteria,
    Verdict,
    complexity_budget,
    diff_discipline,
)

FIXTURES = json.loads(
    (Path(__file__).resolve().parents[3] / "fixtures/assurance-parity.json").read_text()
)


def signature(verdict: Verdict) -> list[list[str]]:
    """The comparable shape of a verdict: (code, severity) pairs, sorted.

    Order is not part of the contract -- the two implementations iterate their
    pattern tables in the same order today, and pinning that would make the
    fixture fail for a reason nobody cares about.
    """
    return sorted([v.code, v.severity] for v in verdict.violations)


def ids(section: str) -> list[str]:
    return [case["name"] for case in FIXTURES[section]]


@pytest.mark.parametrize("case", FIXTURES["complexity"], ids=ids("complexity"))
def test_complexity_parity(case: dict) -> None:
    options = case["options"]
    verdict = complexity_budget(
        case["code"],
        max_lines=options.get("maxLines"),
        max_definitions=options.get("maxDefinitions"),
        baseline_lines=options.get("baselineLines"),
        **({"max_nesting": options["maxNesting"]} if "maxNesting" in options else {}),
    )
    assert signature(verdict) == sorted(case["expect"])


@pytest.mark.parametrize("case", FIXTURES["diff"], ids=ids("diff"))
def test_diff_parity(case: dict) -> None:
    options = case["options"]
    verdict = diff_discipline(
        case["diff"],
        request_terms=options.get("requestTerms", ()),
        allowed_paths=options.get("allowedPaths"),
        allow_formatting=options.get("allowFormatting", False),
    )
    assert signature(verdict) == sorted(case["expect"])


@pytest.mark.parametrize("case", FIXTURES["criteria"], ids=ids("criteria"))
def test_criteria_parity(case: dict) -> None:
    goals = SuccessCriteria(case["name"])
    for criterion in case["criteria"]:
        verified = criterion["verified"]
        if verified is None:
            goals.require(criterion["description"])
        else:
            goals.require(criterion["description"], lambda v=verified: (v, ""))
    assert signature(goals.check()) == sorted(case["expect"])


@pytest.mark.parametrize("case", FIXTURES["ledger"], ids=ids("ledger"))
def test_ledger_parity(case: dict) -> None:
    ledger = AssumptionLedger(case["task"], trivial=case["trivial"])
    for claim, because in case["assumptions"]:
        ledger.assume(claim, because)
    for question in case["openQuestions"]:
        ledger.ask(question)
    assert signature(ledger.check()) == sorted(case["expect"])


def test_every_emitted_code_is_registered() -> None:
    """No violation may ship without a code -- an empty code breaks parity
    silently, which is the failure mode this whole module exists to prevent."""
    verdicts = [
        AssumptionLedger("x").check(),
        SuccessCriteria("x").require("make it work").check(),
        complexity_budget("class HandlerBase:\n    pass\n", max_lines=0),
        diff_discipline(
            "+++ b/a.py\n@@ -1,2 +1,1 @@\n-# why\n x = 1\n", allowed_paths=["src/"]
        ),
    ]
    for verdict in verdicts:
        for violation in verdict.violations:
            assert violation.code, f"violation without a code: {violation.message}"
            assert "." in violation.code, f"code must be namespaced: {violation.code}"
