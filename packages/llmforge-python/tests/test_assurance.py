"""The four principles, as gates that must actually fail when violated.

Every test here answers one question: if an agent ignored this principle, would
the gate catch it? A gate that only passes is decoration.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from llmforge import Agent, FakeProvider
from llmforge.assurance import (
    AssumptionLedger,
    AssuranceError,
    Postflight,
    Preflight,
    SuccessCriteria,
    complexity_budget,
    diff_discipline,
    gate_agent,
    parse_diff,
    scan_hedging,
)

# --------------------------------------------------------------------------- #
# 1. Think Before Coding
# --------------------------------------------------------------------------- #


def test_empty_ledger_blocks_a_nontrivial_task():
    verdict = AssumptionLedger(task="rewrite the scheduler").check()
    assert not verdict.passed
    assert "no assumptions declared" in verdict.blockers[0].message


def test_trivial_tasks_are_exempt_as_the_guidelines_intend():
    assert AssumptionLedger(task="fix typo", trivial=True).check().passed


def test_unsupported_assumption_warns_but_does_not_block():
    verdict = AssumptionLedger(task="x").assume("the queue is FIFO").check()
    assert verdict.passed
    assert any(v.severity == "warn" for v in verdict.violations)


def test_supported_assumption_is_clean():
    verdict = (
        AssumptionLedger(task="x")
        .assume("the queue is FIFO", because="queue.py:31 uses collections.deque.popleft")
        .check()
    )
    assert verdict.passed and not verdict.violations


def test_open_questions_block_by_default():
    ledger = AssumptionLedger(task="x").assume("a", because="b").ask("which tenant?")
    assert not ledger.check().passed
    assert ledger.check(blocking_questions=False).passed


def test_ledger_renders_unsupported_assumptions_visibly():
    text = AssumptionLedger(task="x").assume("guessed").render()
    assert "UNSUPPORTED" in text


def test_hedging_scan_is_advisory_only():
    verdict = scan_hedging("I assume the ids are unique, so I'll just index on them.")
    assert verdict.passed  # notes, never blockers
    assert verdict.violations


# --------------------------------------------------------------------------- #
# 2. Simplicity First
# --------------------------------------------------------------------------- #


def test_line_budget_blocks_bloat():
    code = "\n".join(f"x{i} = {i}" for i in range(60))
    assert not complexity_budget(code, max_lines=20).passed
    assert complexity_budget(code, max_lines=100).passed


def test_growth_against_baseline_blocks_a_bloated_rewrite():
    """'Implement a bloated construction over 1000 lines when 100 would do.'"""
    code = "\n".join(f"line{i} = {i}" for i in range(100))
    verdict = complexity_budget(code, baseline_lines=20)
    assert not verdict.passed
    assert "5.0x growth" in verdict.blockers[0].message


def test_comments_and_blanks_do_not_count_against_the_budget():
    code = "# a comment\n\n# another\nx = 1\n"
    assert complexity_budget(code, max_lines=1).passed


def test_speculative_generality_is_flagged():
    code = (
        "class AbstractHandlerBase:\n"
        "    pass\n"
        "\n"
        "def make_handler_factory():\n"
        "    pass\n"
    )
    kinds = [v.message for v in complexity_budget(code).violations]
    assert any("abstract-base" in k for k in kinds)
    assert any("factory" in k for k in kinds)


def test_swallowed_exception_is_flagged():
    code = "try:\n    go()\nexcept Exception:\n    pass\n"
    assert any("swallowed-error" in v.message for v in complexity_budget(code).violations)


def test_deep_nesting_warns():
    code = "def f():\n" + "".join(" " * (4 * i) + f"if x{i}:\n" for i in range(1, 7))
    assert any("nesting depth" in v.message for v in complexity_budget(code).violations)


# --------------------------------------------------------------------------- #
# 3. Surgical Changes
# --------------------------------------------------------------------------- #

DIFF_WITH_DRIVE_BY = """\
diff --git a/src/upload.py b/src/upload.py
--- a/src/upload.py
+++ b/src/upload.py
@@ -10,3 +10,4 @@
 def upload(path):
+    retry(3)
     return put(path)
diff --git a/src/colors.py b/src/colors.py
--- a/src/colors.py
+++ b/src/colors.py
@@ -1,2 +1,2 @@
-RED   = "#ff0000"
+RED = "#ff0000"
"""


def test_parse_diff_attributes_hunks_to_the_right_files():
    hunks = parse_diff(DIFF_WITH_DRIVE_BY)
    assert [h.file for h in hunks] == ["src/upload.py", "src/colors.py"]


def test_pure_reformatting_hunk_is_flagged():
    verdict = diff_discipline(DIFF_WITH_DRIVE_BY)
    assert any("pure reformatting" in v.message for v in verdict.violations)


def test_out_of_scope_file_blocks():
    verdict = diff_discipline(DIFF_WITH_DRIVE_BY, allowed_paths=["src/upload.py"])
    assert not verdict.passed
    assert "src/colors.py" in verdict.blockers[0].message


def test_unrelated_file_warns_when_request_terms_are_given():
    verdict = diff_discipline(DIFF_WITH_DRIVE_BY, request_terms=["upload", "retry"])
    assert any("does not obviously relate" in v.message for v in verdict.violations)


def test_deleted_comment_blocks():
    """The specific failure the guidelines name: removing what you didn't understand."""
    diff = (
        "+++ b/src/jwt.py\n"
        "@@ -1,3 +1,2 @@\n"
        "-# leeway is 60s because the auth server clock drifts; see INC-2231\n"
        " def verify(token):\n"
        "     ...\n"
    )
    verdict = diff_discipline(diff)
    assert not verdict.passed
    assert "INC-2231" in verdict.blockers[0].message


def test_comment_moved_not_deleted_is_not_flagged():
    diff = (
        "+++ b/src/jwt.py\n"
        "@@ -1,3 +1,3 @@\n"
        "-# leeway is 60s\n"
        " def verify(token):\n"
        "+# leeway is 60s\n"
    )
    assert diff_discipline(diff).passed


def test_in_scope_change_passes_cleanly():
    diff = (
        "+++ b/src/upload.py\n"
        "@@ -10,2 +10,3 @@\n"
        " def upload(path):\n"
        "+    retry(3)\n"
    )
    assert diff_discipline(diff, request_terms=["upload"], allowed_paths=["src/"]).passed


# --------------------------------------------------------------------------- #
# 4. Goal-Driven Execution
# --------------------------------------------------------------------------- #


def test_no_criteria_blocks():
    assert not SuccessCriteria("do the thing").check().passed


@pytest.mark.parametrize("vague", ["make it work", "fix it", "refactor", "improve"])
def test_vague_criteria_block(vague):
    goals = SuccessCriteria("x").require(vague, lambda: (True, ""))
    assert not goals.check().passed


def test_verifiable_criterion_passes():
    goals = SuccessCriteria("x").require("test_upload_retries passes", lambda: (True, "ok"))
    assert goals.check().passed


def test_criterion_without_verifier_warns_only():
    goals = SuccessCriteria("x").require("the docs mention the retry policy")
    verdict = goals.check()
    assert verdict.passed and any(v.severity == "warn" for v in verdict.violations)


def test_verify_reports_unmet_criteria():
    goals = (
        SuccessCriteria("x")
        .require("a passes", lambda: (True, ""))
        .require("b passes", lambda: (False, "2 failed"))
    )
    verdict = goals.verify()
    assert not verdict.passed
    assert verdict.blockers[0].remedy == "2 failed"


def test_a_raising_verifier_is_a_failed_criterion_not_a_crash():
    def boom() -> tuple[bool, str]:
        raise RuntimeError("pytest not installed")

    verdict = SuccessCriteria("x").require("tests pass", boom).verify()
    assert not verdict.passed and "pytest not installed" in verdict.blockers[0].remedy


def test_plan_renders_in_the_guidelines_step_verify_form():
    goals = SuccessCriteria("x").step("add retry", "unit test passes")
    assert "add retry -> verify: unit test passes" in goals.render()


# --------------------------------------------------------------------------- #
# composition
# --------------------------------------------------------------------------- #


def test_preflight_with_nothing_supplied_blocks():
    assert not Preflight().run().passed


def test_preflight_combines_both_gates():
    verdict = Preflight(
        ledger=AssumptionLedger(task="x", trivial=True),
        goals=SuccessCriteria("x").require("t passes", lambda: (True, "")),
    ).run()
    assert verdict.passed


def test_postflight_combines_diff_and_complexity():
    verdict = Postflight(
        diff=DIFF_WITH_DRIVE_BY,
        code="x = 1\n" * 50,
        allowed_paths=["src/upload.py"],
        max_lines=10,
    ).run()
    messages = [v.message for v in verdict.blockers]
    assert any("outside the declared scope" in m for m in messages)
    assert any("exceeds the declared budget" in m for m in messages)


def test_gate_agent_refuses_to_run_without_criteria():
    agent = gate_agent(Agent(FakeProvider(["ok"])), SuccessCriteria("x"))
    with pytest.raises(AssuranceError, match="no success criteria"):
        agent.run("go")


def test_gate_agent_allows_a_well_specified_run():
    goals = SuccessCriteria("x").require("output is ok", lambda: (True, ""))
    agent = gate_agent(Agent(FakeProvider(["ok"])), goals)
    assert agent.run("go").text == "ok"


def test_verdict_is_falsy_when_blocked_and_truthy_when_clean():
    assert not SuccessCriteria("x").check()
    assert SuccessCriteria("x").require("t passes", lambda: (True, "")).check()


# --------------------------------------------------------------------------- #
# the gates, applied to this repository
# --------------------------------------------------------------------------- #

#: Paths whose entire purpose is to contain bad patterns.
_EXEMPT = ("test_assurance.py", "02_gate_catches_a_drive_by.py")


def _own_sources() -> list[Path]:
    root = Path(__file__).resolve().parents[3]
    return [
        path
        for path in (root / "packages/llmforge-python/src").rglob("*.py")
        if not path.name.startswith("_") or path.name == "__init__.py"
    ] + [
        path
        for path in (root / "examples").glob("*.py")
        if path.name not in _EXEMPT
    ]


@pytest.mark.parametrize("path", _own_sources(), ids=lambda p: p.name)
def test_our_own_source_passes_the_gates_we_ship(path: Path) -> None:
    """A gate that fires constantly on its own repository is a gate that gets
    ignored, and an ignored gate is worse than none -- it still occupies the
    slot. This caught `unused-config-knob`, which matched every `model:` in the
    codebase (`model` contains `mode`) while claiming to detect a cross-file
    fact no single-file regex can see.
    """
    verdict = complexity_budget(path.read_text(encoding="utf-8"))
    # `warn` is advisory by construction -- nesting and definition counts are
    # judgment calls, and contorting real code to silence them would be the
    # tail wagging the dog. Blockers and notes are the classes that must stay
    # clean, because those are the ones that claim to have found a defect.
    findings = [
        f"{path.name}:{v.location or '?'} [{v.severity}] {v.message}"
        for v in verdict.violations
        if v.severity in ("blocker", "note")
    ]
    assert not findings, "gates fire on our own source:\n" + "\n".join(findings)
