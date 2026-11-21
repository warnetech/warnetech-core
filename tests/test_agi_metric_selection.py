"""Metric-informed tool selection (MTS). Ranking may change the executed tool only when enabled."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from warnetech_core.agi.orchestrator import AGIOrchestrator
from warnetech_core.agi.planning import Goal
from warnetech_core.agi.state import StateError
from warnetech_core.agi.telemetry import Telemetry
from warnetech_core.agi.tool_metrics import ToolMetrics
from warnetech_core.agi.tools import ToolDefinition, ToolRegistry

REASON_OK = '{"step": 1, "thought": "think", "confidence": 0.9}\n{"conclusion": "answer"}'
REFLECT_PASS = '{"critiques": [], "overall_quality": 0.9, "should_retry": false}'


def _plan(tool_hint: str | None) -> str:
    task = {"title": "research", "description": "look"}
    if tool_hint is not None:
        task["tool_hint"] = tool_hint
    return json.dumps([task])


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


def _registry(executed: list[str], *tools: ToolDefinition) -> ToolRegistry:
    registry = ToolRegistry()
    for tool in tools:
        async def handler(_name=tool.name, **_kwargs):
            executed.append(_name)
            return "ok"

        registry.register(tool, handler=handler)
    return registry


def _seed_metrics(state_dir, outcomes: dict[str, tuple[int, int]]) -> None:
    """outcomes maps tool name -> (successes, failures)."""
    metrics = ToolMetrics()
    for name, (wins, losses) in outcomes.items():
        for _ in range(wins):
            metrics.record(name, success=True)
        for _ in range(losses):
            metrics.record(name, success=False)
    state_dir.mkdir(parents=True, exist_ok=True)
    metrics.save(state_dir / "tool_metrics.json")


def _executed_tools(result) -> list[str]:
    return [r.tool_name for c in result.cycles for r in c.tool_results]


# MTS-AC1 and MTS-AC7: disabled (the default) ignores metrics.


async def test_default_off_executes_hint_even_when_metrics_favour_alternative(tmp_path):
    state = tmp_path / "state"
    _seed_metrics(state, {"hint": (0, 5), "alt": (5, 0)})
    executed: list[str] = []
    registry = _registry(
        executed,
        ToolDefinition(name="hint", selection_group="g"),
        ToolDefinition(name="alt", selection_group="g"),
    )
    orch = AGIOrchestrator(
        _Gateway(_plan("hint")),  # type: ignore[arg-type]
        tool_registry=registry,
        enable_phase2=True,
        state_dir=state,
    )

    result = await orch.run(Goal(id="g1", description="x"))

    assert _executed_tools(result) == ["hint"]
    assert executed == ["hint"]


# MTS-AC2 and MTS-AC6: enabled, a higher-ranked compatible alternative executes.
# This is the red test: runtime selection ignores ToolMetrics today.


async def test_enabled_higher_ranked_compatible_alternative_executes(tmp_path):
    state = tmp_path / "state"
    _seed_metrics(state, {"hint": (0, 5), "alt": (5, 0)})
    executed: list[str] = []
    registry = _registry(
        executed,
        ToolDefinition(name="hint", selection_group="g"),
        ToolDefinition(name="alt", selection_group="g"),
    )
    orch = AGIOrchestrator(
        _Gateway(_plan("hint")),  # type: ignore[arg-type]
        tool_registry=registry,
        enable_phase2=True,
        state_dir=state,
        enable_metric_selection=True,
    )

    result = await orch.run(Goal(id="g2", description="x"))

    assert _executed_tools(result) == ["alt"]
    assert executed == ["alt"]


# MTS-AC3: a higher-ranked tool outside the group never executes.


async def test_ineligible_high_ranked_tool_never_executes(tmp_path):
    state = tmp_path / "state"
    _seed_metrics(state, {"hint": (0, 5), "outsider": (50, 0)})
    executed: list[str] = []
    registry = _registry(
        executed,
        ToolDefinition(name="hint", selection_group="g"),
        ToolDefinition(name="outsider", selection_group=None),
    )
    orch = AGIOrchestrator(
        _Gateway(_plan("hint")),  # type: ignore[arg-type]
        tool_registry=registry,
        enable_phase2=True,
        state_dir=state,
        enable_metric_selection=True,
    )

    result = await orch.run(Goal(id="g3", description="x"))

    assert _executed_tools(result) == ["hint"]


# MTS-AC5: no group means the hint executes, even when enabled.


async def test_enabled_hint_without_group_executes(tmp_path):
    state = tmp_path / "state"
    _seed_metrics(state, {"hint": (0, 5), "other": (9, 0)})
    executed: list[str] = []
    registry = _registry(
        executed,
        ToolDefinition(name="hint"),
        ToolDefinition(name="other", selection_group="g"),
    )
    orch = AGIOrchestrator(
        _Gateway(_plan("hint")),  # type: ignore[arg-type]
        tool_registry=registry,
        enable_phase2=True,
        state_dir=state,
        enable_metric_selection=True,
    )

    result = await orch.run(Goal(id="g4", description="x"))

    assert _executed_tools(result) == ["hint"]


# MTS-AC6: unknown hint keeps the existing no-tool behaviour; no registry fallback.


async def test_enabled_unknown_hint_does_not_fall_back_to_registry(tmp_path):
    state = tmp_path / "state"
    _seed_metrics(state, {"best": (9, 0)})
    executed: list[str] = []
    registry = _registry(executed, ToolDefinition(name="best", selection_group="g"))
    orch = AGIOrchestrator(
        _Gateway(_plan("missing")),  # type: ignore[arg-type]
        tool_registry=registry,
        enable_phase2=True,
        state_dir=state,
        enable_metric_selection=True,
    )

    result = await orch.run(Goal(id="g5", description="x"))

    assert _executed_tools(result) == []
    assert executed == []


# MTS-AC7: an untried alternative beats a hint with a failure history (cold-start prior).


async def test_untried_alternative_beats_failing_hint(tmp_path):
    state = tmp_path / "state"
    _seed_metrics(state, {"hint": (0, 5)})
    executed: list[str] = []
    registry = _registry(
        executed,
        ToolDefinition(name="hint", selection_group="g"),
        ToolDefinition(name="fresh", selection_group="g"),
    )
    orch = AGIOrchestrator(
        _Gateway(_plan("hint")),  # type: ignore[arg-type]
        tool_registry=registry,
        enable_phase2=True,
        state_dir=state,
        enable_metric_selection=True,
    )

    result = await orch.run(Goal(id="g6", description="x"))

    assert _executed_tools(result) == ["fresh"]


# MTS-AC8: equal ranking keeps candidate order, so the hint wins.


async def test_equal_ranking_keeps_hint_first(tmp_path):
    state = tmp_path / "state"
    _seed_metrics(state, {})
    executed: list[str] = []
    registry = _registry(
        executed,
        ToolDefinition(name="hint", selection_group="g"),
        ToolDefinition(name="alt", selection_group="g"),
    )
    orch = AGIOrchestrator(
        _Gateway(_plan("hint")),  # type: ignore[arg-type]
        tool_registry=registry,
        enable_phase2=True,
        state_dir=state,
        enable_metric_selection=True,
    )

    result = await orch.run(Goal(id="g7", description="x"))

    assert _executed_tools(result) == ["hint"]


# MTS-AC10: selection does not change metrics. Outcomes are recorded only for the tool that ran.


async def test_selection_records_outcome_only_for_executed_tool(tmp_path):
    state = tmp_path / "state"
    _seed_metrics(state, {"hint": (0, 5), "alt": (5, 0)})
    executed: list[str] = []
    registry = _registry(
        executed,
        ToolDefinition(name="hint", selection_group="g"),
        ToolDefinition(name="alt", selection_group="g"),
    )
    orch = AGIOrchestrator(
        _Gateway(_plan("hint")),  # type: ignore[arg-type]
        tool_registry=registry,
        enable_phase2=True,
        state_dir=state,
        enable_metric_selection=True,
    )

    await orch.run(Goal(id="g8", description="x"))

    reloaded = ToolMetrics.load(state / "tool_metrics.json")
    assert reloaded.stats("hint").failures == 5
    assert reloaded.stats("hint").successes == 0
    assert reloaded.stats("alt").successes == 6


# MTS-AC11: restart with the same metrics selects the same tool.


async def test_restart_selects_the_same_tool(tmp_path):
    state = tmp_path / "state"
    _seed_metrics(state, {"hint": (0, 5), "alt": (5, 0)})
    executed: list[str] = []
    for goal_id in ("first", "second"):
        registry = _registry(
            executed,
            ToolDefinition(name="hint", selection_group="g"),
            ToolDefinition(name="alt", selection_group="g"),
        )
        orch = AGIOrchestrator(
            _Gateway(_plan("hint")),  # type: ignore[arg-type]
            tool_registry=registry,
            enable_phase2=True,
            state_dir=state,
            enable_metric_selection=True,
        )
        await orch.run(Goal(id=goal_id, description="x"))
    # Each run records outcomes, so only check that the choice stayed on the alternative.
    assert executed == ["alt", "alt"]


# MTS-AC12: tool_selected reports the winner and the selection mode.


async def test_tool_selected_reports_metrics_mode(tmp_path):
    state = tmp_path / "state"
    _seed_metrics(state, {"hint": (0, 5), "alt": (5, 0)})
    telemetry_path = tmp_path / "events.jsonl"
    registry = _registry(
        [],
        ToolDefinition(name="hint", selection_group="g"),
        ToolDefinition(name="alt", selection_group="g"),
    )
    orch = AGIOrchestrator(
        _Gateway(_plan("hint")),  # type: ignore[arg-type]
        tool_registry=registry,
        telemetry=Telemetry(telemetry_path),
        enable_phase2=True,
        state_dir=state,
        enable_metric_selection=True,
    )

    await orch.run(Goal(id="g9", description="x"))

    selected = [
        json.loads(line)
        for line in telemetry_path.read_text(encoding="utf-8").splitlines()
        if json.loads(line)["event"] == "tool_selected"
    ]
    assert len(selected) == 1
    assert selected[0]["payload"]["tool_name"] == "alt"
    assert selected[0]["payload"]["selection_mode"] == "metrics"


# Constructor contract and MTS-AC9: corrupt metrics stay a visible error.


def test_metric_selection_requires_phase2_state(tmp_path):
    with pytest.raises(ValueError):
        AGIOrchestrator(
            _Gateway(_plan("hint")),  # type: ignore[arg-type]
            enable_metric_selection=True,
        )


def test_corrupt_metrics_raise_state_error_when_enabled(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    (state / "tool_metrics.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(StateError):
        AGIOrchestrator(
            _Gateway(_plan("hint")),  # type: ignore[arg-type]
            enable_phase2=True,
            state_dir=state,
            enable_metric_selection=True,
        )
