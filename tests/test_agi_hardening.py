"""Regression tests for AGI parsing and the orchestrator loop.

LLM output is untrusted: stray lines, out-of-range numbers and wrong JSON shapes
must degrade to a sane default, never crash the caller. Every test here runs
offline against a scripted gateway.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

from warnetech_core.agi.orchestrator import AGIOrchestrator
from warnetech_core.agi.planning import Goal, Planner, PlanStatus
from warnetech_core.agi.reasoning import ReasoningEngine, ReasoningStrategy
from warnetech_core.agi.reflection import CritiqueSeverity, Reflector

COT = ReasoningStrategy.CHAIN_OF_THOUGHT


class ScriptedGateway:
    """Routes each completion by its system prompt, so the test controls every LLM reply."""

    def __init__(self, *, plan: str, reasoning: str, reflection: str) -> None:
        self.plan = plan
        self.reasoning = reasoning
        self.reflection = reflection
        self.calls: list[str] = []

    async def complete(self, model, messages, *, system=None, **_kw):
        system = system or ""
        if "task planner" in system:
            self.calls.append("plan")
            text = self.plan
        elif "reasoning engine" in system:
            self.calls.append("reason")
            text = self.reasoning
        else:
            self.calls.append("reflect")
            text = self.reflection
        return SimpleNamespace(text=text)


# ---------------------------------------------------------------- planning


class TestPlannerParsing:
    def test_non_dict_entries_are_skipped_and_dependencies_still_resolve(self):
        planner = Planner(MagicMock())
        tasks = planner._parse_tasks('[{"title": "a"}, 5, {"title": "b", "depends_on": ["a"]}]')
        assert [t.title for t in tasks] == ["a", "b"]
        assert tasks[1].depends_on == [tasks[0].id]

    def test_unknown_dependency_titles_are_dropped(self):
        planner = Planner(MagicMock())
        tasks = planner._parse_tasks('[{"title": "a", "depends_on": ["ghost"]}]')
        assert tasks[0].depends_on == []

    def test_non_list_output_yields_no_tasks(self):
        planner = Planner(MagicMock())
        assert planner._parse_tasks('{"title": "a"}') == []

    def test_invalid_json_yields_no_tasks(self):
        planner = Planner(MagicMock())
        assert planner._parse_tasks("not json at all") == []


# --------------------------------------------------------------- reasoning


class TestReasoningParsing:
    def _engine(self) -> ReasoningEngine:
        return ReasoningEngine(MagicMock())

    def test_bare_number_line_is_ignored(self):
        steps, conclusion = self._engine()._parse_response(
            '42\n{"step": 1, "thought": "t", "confidence": 0.9}\n{"conclusion": "done"}',
            COT,
            10,
        )
        assert conclusion == "done"
        assert len(steps) == 1

    def test_out_of_range_confidence_is_clamped(self):
        steps, _ = self._engine()._parse_response(
            '{"step": 1, "thought": "t", "confidence": 1.5}\n'
            '{"step": 2, "thought": "u", "confidence": -0.3}',
            COT,
            10,
        )
        assert [s.confidence for s in steps] == [1.0, 0.0]

    def test_non_numeric_confidence_falls_back_to_default(self):
        steps, _ = self._engine()._parse_response(
            '{"step": 1, "thought": "t", "confidence": "high"}', COT, 10
        )
        assert steps[0].confidence == 0.5

    def test_conclusion_falls_back_to_last_step(self):
        steps, conclusion = self._engine()._parse_response(
            '{"step": 1, "thought": "only step"}', COT, 10
        )
        assert conclusion == "only step"
        assert len(steps) == 1


# -------------------------------------------------------------- reflection


class TestReflectionParsing:
    def _reflector(self) -> Reflector:
        return Reflector(MagicMock())

    def test_json_list_is_treated_as_parse_failure(self):
        result = self._reflector()._parse_reflection("[1, 2]")
        assert result.critiques[0].aspect == "parse_failure"
        assert result.overall_quality == 0.5

    def test_null_quality_uses_default(self):
        result = self._reflector()._parse_reflection('{"overall_quality": null}')
        assert result.overall_quality == 0.5

    def test_non_string_revised_output_is_dropped(self):
        result = self._reflector()._parse_reflection(
            '{"overall_quality": 0.9, "revised_output": {"a": 1}}'
        )
        assert result.revised_output is None
        assert result.overall_quality == 0.9

    def test_out_of_range_quality_is_clamped(self):
        result = self._reflector()._parse_reflection('{"overall_quality": 7}')
        assert result.overall_quality == 1.0

    def test_bad_critique_entries_are_skipped_not_fatal(self):
        result = self._reflector()._parse_reflection(
            json.dumps(
                {
                    "critiques": [
                        "not an object",
                        {"aspect": "x", "severity": "bogus", "description": "d"},
                        {"aspect": "y", "severity": "error", "description": "real"},
                    ],
                    "overall_quality": 0.4,
                }
            )
        )
        assert [c.aspect for c in result.critiques] == ["y"]
        assert result.critiques[0].severity == CritiqueSeverity.ERROR


# ------------------------------------------------------------ orchestrator


PLAN_TWO_TASKS = json.dumps(
    [
        {"title": "research", "description": "look it up"},
        {"title": "write", "description": "draft it", "depends_on": ["research"]},
    ]
)
REASON_OK = '{"step": 1, "thought": "think", "confidence": 0.9}\n{"conclusion": "answer"}'
REFLECT_PASS = '{"critiques": [], "overall_quality": 0.9, "should_retry": false}'
REFLECT_FAIL = (
    '{"critiques": [{"aspect": "correctness", "severity": "critical", '
    '"description": "wrong"}], "overall_quality": 0.1, "should_retry": false}'
)


class TestOrchestratorLoop:
    async def test_completes_all_tasks_in_dependency_order(self):
        gw = ScriptedGateway(plan=PLAN_TWO_TASKS, reasoning=REASON_OK, reflection=REFLECT_PASS)
        orch = AGIOrchestrator(gw)  # type: ignore[arg-type]

        result = await orch.run(Goal(description="ship it"))

        assert result.success is True
        assert result.plan.status == PlanStatus.COMPLETED
        assert [c.task_title for c in result.cycles] == ["research", "write"]
        assert all(c.outcome == "completed" for c in result.cycles)
        assert gw.calls == ["plan", "reason", "reflect", "reason", "reflect"]

    async def test_critical_reflection_fails_the_task_and_the_run(self):
        gw = ScriptedGateway(plan=PLAN_TWO_TASKS, reasoning=REASON_OK, reflection=REFLECT_FAIL)
        orch = AGIOrchestrator(gw)  # type: ignore[arg-type]

        result = await orch.run(Goal(description="ship it"))

        assert result.success is False
        # A low score asks for a retry; once retries are spent, the critical error fails the task.
        assert [c.outcome for c in result.cycles] == ["retry", "retry", "failed"]
        # The dependent task never becomes ready, so it is never attempted.
        assert all(c.task_title == "research" for c in result.cycles)

    async def test_retry_is_bounded_by_max_retries_per_task(self):
        retry = '{"critiques": [], "overall_quality": 0.2, "should_retry": true}'
        gw = ScriptedGateway(
            plan=json.dumps([{"title": "only", "description": "d"}]),
            reasoning=REASON_OK,
            reflection=retry,
        )
        orch = AGIOrchestrator(gw, max_retries_per_task=2, max_cycles=20)  # type: ignore[arg-type]

        result = await orch.run(Goal(description="g"))

        outcomes = [c.outcome for c in result.cycles]
        assert outcomes == ["retry", "retry", "completed"]
        assert result.success is True

    async def test_empty_plan_returns_unsuccessful_result(self):
        gw = ScriptedGateway(plan="[]", reasoning=REASON_OK, reflection=REFLECT_PASS)
        orch = AGIOrchestrator(gw)  # type: ignore[arg-type]

        result = await orch.run(Goal(description="g"))

        assert result.success is False
        assert result.summary == "Planner produced no tasks"
        assert gw.calls == ["plan"]

    async def test_memory_failures_do_not_stop_the_run(self):
        gw = ScriptedGateway(plan=PLAN_TWO_TASKS, reasoning=REASON_OK, reflection=REFLECT_PASS)
        memory = MagicMock()
        memory.get_context = _raiser
        memory.remember = _raiser
        orch = AGIOrchestrator(gw, memory)  # type: ignore[arg-type]

        result = await orch.run(Goal(description="g"))

        assert result.success is True


async def _raiser(*_args, **_kwargs):
    raise RuntimeError("memory store is down")
