"""Integration tests for AGI orchestrator wiring: telemetry, confidence and capabilities (T6).

Every test runs offline against a scripted gateway. Telemetry is opt-in: without a sink the
run must behave as before, and with one the events must appear in the order the loop emits them.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from warnetech_core.agi.capability_registry import CapabilityRegistry
from warnetech_core.agi.orchestrator import AGIOrchestrator
from warnetech_core.agi.planning import Goal
from warnetech_core.agi.telemetry import Telemetry
from warnetech_core.agi.tools import ToolDefinition, ToolRegistry

PLAN_TWO_TASKS = json.dumps(
    [
        {"title": "research", "description": "look it up"},
        {"title": "write", "description": "draft it", "depends_on": ["research"]},
    ]
)
PLAN_TOOL_TASK = json.dumps(
    [{"title": "research", "description": "look it up", "tool_hint": "lookup"}]
)
PLAN_ONE_TASK = json.dumps([{"title": "only", "description": "d"}])
REASON_OK = '{"step": 1, "thought": "think", "confidence": 0.9}\n{"conclusion": "answer"}'
REFLECT_PASS = '{"critiques": [], "overall_quality": 0.9, "should_retry": false}'
REFLECT_FAIL = (
    '{"critiques": [{"aspect": "correctness", "severity": "critical", '
    '"description": "wrong"}], "overall_quality": 0.1, "should_retry": false}'
)
REFLECT_RETRY = '{"critiques": [], "overall_quality": 0.2, "should_retry": true}'
REFLECT_WARNING = (
    '{"critiques": [{"aspect": "style", "severity": "warning", "description": "meh"}], '
    '"overall_quality": 0.9, "should_retry": false}'
)


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


def _gateway(plan: str, reflection: str = REFLECT_PASS) -> ScriptedGateway:
    return ScriptedGateway(plan=plan, reasoning=REASON_OK, reflection=reflection)


def _read_events(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _lookup_registry() -> ToolRegistry:
    registry = ToolRegistry()

    async def lookup(**_kwargs):
        return "data"

    registry.register(ToolDefinition(name="lookup"), handler=lookup)
    return registry


# ------------------------------------------------------- without telemetry


class TestWithoutTelemetry:
    async def test_result_matches_pre_change_behaviour(self):
        gw = _gateway(PLAN_TWO_TASKS)
        orch = AGIOrchestrator(gw)  # type: ignore[arg-type]

        result = await orch.run(Goal(description="ship it"))

        assert result.success is True
        assert result.summary == "Completed: 2/2 tasks done. Cycles: 2"
        assert [c.outcome for c in result.cycles] == ["completed", "completed"]
        assert [c.task_title for c in result.cycles] == ["research", "write"]
        assert gw.calls == ["plan", "reason", "reflect", "reason", "reflect"]
        # Confidence is a result field, so it is set even without a telemetry sink.
        assert result.confidence == 0.6

    async def test_capabilities_do_not_change_the_result(self):
        plain = await AGIOrchestrator(_gateway(PLAN_TWO_TASKS)).run(  # type: ignore[arg-type]
            Goal(description="g")
        )
        with_caps = await AGIOrchestrator(  # type: ignore[arg-type]
            _gateway(PLAN_TWO_TASKS), capabilities=CapabilityRegistry()
        ).run(Goal(description="g"))

        assert with_caps.summary == plain.summary
        assert with_caps.success == plain.success
        assert with_caps.confidence == plain.confidence
        assert [c.outcome for c in with_caps.cycles] == [c.outcome for c in plain.cycles]


# ------------------------------------------------------------ telemetry


class TestTelemetryEvents:
    async def test_events_for_two_task_run_appear_in_order(self, tmp_path: Path):
        path = tmp_path / "telemetry.jsonl"
        orch = AGIOrchestrator(_gateway(PLAN_TWO_TASKS), telemetry=Telemetry(path))  # type: ignore[arg-type]
        goal = Goal(description="ship it")

        result = await orch.run(goal)

        events = _read_events(path)
        assert [e["event"] for e in events] == [
            "goal_received",
            "plan_generated",
            "reflection_completed",
            "reflection_completed",
            "execution_completed",
        ]
        assert {e["goal_id"] for e in events} == {goal.id}
        final = events[-1]["payload"]
        assert final["success"] is True
        assert final["cycles"] == 2
        assert final["confidence"] == result.confidence == 0.6

    async def test_tool_selected_is_emitted_between_plan_and_reflection(self, tmp_path: Path):
        path = tmp_path / "telemetry.jsonl"
        orch = AGIOrchestrator(  # type: ignore[arg-type]
            _gateway(PLAN_TOOL_TASK),
            tool_registry=_lookup_registry(),
            telemetry=Telemetry(path),
        )

        await orch.run(Goal(description="g"))

        events = _read_events(path)
        assert [e["event"] for e in events] == [
            "goal_received",
            "plan_generated",
            "tool_selected",
            "reflection_completed",
            "execution_completed",
        ]
        assert events[2]["payload"]["tool_name"] == "lookup"

    async def test_retries_and_failure_emit_one_reflection_event_per_cycle(self, tmp_path: Path):
        path = tmp_path / "telemetry.jsonl"
        orch = AGIOrchestrator(  # type: ignore[arg-type]
            _gateway(PLAN_TWO_TASKS, REFLECT_FAIL), telemetry=Telemetry(path)
        )

        result = await orch.run(Goal(description="g"))

        events = _read_events(path)
        names = [e["event"] for e in events]
        assert names == [
            "goal_received",
            "plan_generated",
            "reflection_completed",
            "reflection_completed",
            "reflection_completed",
            "execution_completed",
        ]
        assert len(result.cycles) == 3
        assert events[-1]["payload"]["success"] is False

    async def test_empty_plan_still_emits_a_terminal_event(self, tmp_path: Path):
        path = tmp_path / "telemetry.jsonl"
        orch = AGIOrchestrator(_gateway("[]"), telemetry=Telemetry(path))  # type: ignore[arg-type]

        result = await orch.run(Goal(description="g"))

        assert result.confidence is None
        events = _read_events(path)
        assert [e["event"] for e in events] == [
            "goal_received",
            "plan_generated",
            "execution_completed",
        ]
        assert events[-1]["payload"] == {"success": False, "cycles": 0, "confidence": None}

    async def test_unwritable_sink_does_not_break_the_run(self, tmp_path: Path):
        blocker = tmp_path / "not-a-directory"
        blocker.write_text("x", encoding="utf-8")
        orch = AGIOrchestrator(  # type: ignore[arg-type]
            _gateway(PLAN_TWO_TASKS), telemetry=Telemetry(blocker / "telemetry.jsonl")
        )

        result = await orch.run(Goal(description="g"))

        assert result.success is True
        assert result.summary == "Completed: 2/2 tasks done. Cycles: 2"


# ----------------------------------------------------------- confidence


class TestConfidenceMapping:
    """Known runs checked against the D4 mapping in ConfidenceScorer (base 0.50)."""

    async def test_critical_critiques_count_as_contradictions(self):
        orch = AGIOrchestrator(_gateway(PLAN_TWO_TASKS, REFLECT_FAIL))  # type: ignore[arg-type]

        result = await orch.run(Goal(description="g"))

        # Three cycles, each with one critical critique, and no completed tasks: 0.50 - 3 * 0.10.
        assert result.confidence == 0.2

    async def test_completed_clean_task_after_retries_counts_once_as_evidence(self):
        orch = AGIOrchestrator(_gateway(PLAN_ONE_TASK, REFLECT_RETRY))  # type: ignore[arg-type]

        result = await orch.run(Goal(description="g"))

        assert [c.outcome for c in result.cycles] == ["retry", "retry", "completed"]
        # Only the final completed task has no critiques: 0.50 + 0.05.
        assert result.confidence == 0.55

    async def test_completed_task_with_a_critique_is_not_evidence(self):
        orch = AGIOrchestrator(_gateway(PLAN_ONE_TASK, REFLECT_WARNING))  # type: ignore[arg-type]

        result = await orch.run(Goal(description="g"))

        assert result.success is True
        assert result.confidence == 0.5

    async def test_successful_tool_result_counts_as_evidence(self):
        orch = AGIOrchestrator(  # type: ignore[arg-type]
            _gateway(PLAN_TOOL_TASK), tool_registry=_lookup_registry()
        )

        result = await orch.run(Goal(description="g"))

        # One successful tool result plus one clean completed task: 0.50 + 0.05 + 0.05.
        assert result.confidence == 0.6
