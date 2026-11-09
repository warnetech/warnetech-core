"""P2-7 gate tests: opt-in Phase 2 wiring in AGIOrchestrator (AC5, AC6, AC7, failure path)."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from warnetech_core.agi.orchestrator import AGIOrchestrator
from warnetech_core.agi.planning import Goal
from warnetech_core.agi.state import AgentStateStore, ReflectionHistory
from warnetech_core.agi.telemetry import Telemetry
from warnetech_core.agi.tool_metrics import ToolMetrics
from warnetech_core.agi.tools import ToolDefinition, ToolRegistry

PLAN_ONE_TASK = json.dumps([{"title": "only", "description": "d"}])
PLAN_TOOL_TASK = json.dumps([{"title": "research", "description": "look", "tool_hint": "lookup"}])
REASON_OK = '{"step": 1, "thought": "think", "confidence": 0.9}\n{"conclusion": "answer"}'
REFLECT_PASS = '{"critiques": [], "overall_quality": 0.9, "should_retry": false}'


class _Gateway:
    def __init__(self, plan: str) -> None:
        self.plan = plan

    async def complete(self, model, messages, *, system=None, **_kw):
        system = system or ""
        if "task planner" in system:
            text = self.plan
        elif "reasoning engine" in system:
            text = REASON_OK
        else:
            text = REFLECT_PASS
        return SimpleNamespace(text=text)


def _lookup_registry() -> ToolRegistry:
    registry = ToolRegistry()

    async def lookup(**_kwargs):
        return "data"

    registry.register(ToolDefinition(name="lookup"), handler=lookup)
    return registry


def _events(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


# --- constructor flag -------------------------------------------------------


def test_state_dir_without_enable_flag_is_rejected(tmp_path):
    with pytest.raises(ValueError):
        AGIOrchestrator(_Gateway(PLAN_ONE_TASK), state_dir=tmp_path)  # type: ignore[arg-type]


def test_enable_flag_without_state_dir_is_rejected():
    with pytest.raises(ValueError):
        AGIOrchestrator(_Gateway(PLAN_ONE_TASK), enable_phase2=True)  # type: ignore[arg-type]


# --- AC5: off path is identical to Phase 1 ---------------------------------


async def test_ac5_default_run_matches_enabled_run_result_and_writes_nothing_when_off(tmp_path):
    off_telemetry = tmp_path / "off.jsonl"
    off = AGIOrchestrator(
        _Gateway(PLAN_TOOL_TASK),  # type: ignore[arg-type]
        tool_registry=_lookup_registry(),
        telemetry=Telemetry(off_telemetry),
    )
    off_result = await off.run(Goal(id="g", description="ship it"))

    state = tmp_path / "state"
    on = AGIOrchestrator(
        _Gateway(PLAN_TOOL_TASK),  # type: ignore[arg-type]
        tool_registry=_lookup_registry(),
        enable_phase2=True,
        state_dir=state,
    )
    on_result = await on.run(Goal(id="g", description="ship it"))

    assert on_result.success == off_result.success
    assert on_result.summary == off_result.summary
    assert on_result.confidence == off_result.confidence
    assert [c.outcome for c in on_result.cycles] == [c.outcome for c in off_result.cycles]

    # Off path wrote no Phase 2 files.
    assert not (tmp_path / "goals.json").exists()
    assert not any(p.name.endswith(".json") for p in tmp_path.iterdir())
    assert not hasattr(off, "goal_store")
    assert [e["event"] for e in _events(off_telemetry)] == [
        "goal_received",
        "plan_generated",
        "tool_selected",
        "reflection_completed",
        "execution_completed",
    ]


# --- AC6: enabled run writes goal start/end and one reflection ------------


async def test_ac6_enabled_run_writes_goal_start_end_and_reflection(tmp_path):
    state = tmp_path / "state"
    orch = AGIOrchestrator(
        _Gateway(PLAN_ONE_TASK),  # type: ignore[arg-type]
        enable_phase2=True,
        state_dir=state,
    )
    result = await orch.run(Goal(id="goal-1", description="ship it"))

    assert result.success is True
    statuses = [r.status for r in AgentStateStore(state).goal_history("goal-1")]
    assert statuses == ["running", "completed"]
    reflections = ReflectionHistory(state).history("goal-1")
    assert len(reflections) == 1
    assert reflections[0].overall_quality == pytest.approx(0.9)


async def test_ac6_tool_outcomes_are_recorded_and_saved(tmp_path):
    state = tmp_path / "state"
    orch = AGIOrchestrator(
        _Gateway(PLAN_TOOL_TASK),  # type: ignore[arg-type]
        tool_registry=_lookup_registry(),
        enable_phase2=True,
        state_dir=state,
    )
    await orch.run(Goal(id="goal-2", description="look"))

    reloaded = ToolMetrics.load(state / "tool_metrics.json")
    assert reloaded.stats("lookup").successes == 1
    assert reloaded.stats("lookup").failures == 0


# --- AC7: restart recovery -------------------------------------------------


async def test_ac7_records_survive_a_new_orchestrator(tmp_path):
    state = tmp_path / "state"
    first = AGIOrchestrator(
        _Gateway(PLAN_TOOL_TASK),  # type: ignore[arg-type]
        tool_registry=_lookup_registry(),
        enable_phase2=True,
        state_dir=state,
    )
    await first.run(Goal(id="goal-3", description="look"))
    del first

    second = AGIOrchestrator(
        _Gateway(PLAN_TOOL_TASK),  # type: ignore[arg-type]
        tool_registry=_lookup_registry(),
        enable_phase2=True,
        state_dir=state,
    )
    assert second.tool_metrics.stats("lookup").successes == 1
    assert AgentStateStore(state).latest_status("goal-3") == "completed"


# --- failure path: persistence errors never break the run -----------------


async def test_persistence_failure_emits_bounded_event_and_run_continues(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    # A directory where goals.json should be makes every goal write fail.
    (state / "goals.json").mkdir()
    telemetry_path = tmp_path / "events.jsonl"
    orch = AGIOrchestrator(
        _Gateway(PLAN_ONE_TASK),  # type: ignore[arg-type]
        telemetry=Telemetry(telemetry_path),
        enable_phase2=True,
        state_dir=state,
    )

    result = await orch.run(Goal(id="goal-4", description="ship it"))

    assert result.success is True
    failures = [e for e in _events(telemetry_path) if e["event"] == "persistence_failed"]
    assert failures, "expected a persistence_failed event"
    for event in failures:
        payload = event["payload"]
        assert set(payload) == {"component", "error_type"}
        assert payload["component"] == "goal_store"
        assert payload["error_type"] == "IsADirectoryError"
    # Execution and reflection still completed around the failed write.
    names = [e["event"] for e in _events(telemetry_path)]
    assert "execution_completed" in names
    assert "reflection_completed" in names
