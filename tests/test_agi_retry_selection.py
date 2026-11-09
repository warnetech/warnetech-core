"""PB-4: an ordinary retry keeps the tool selected for the original attempt.

Retry does not re-run candidate selection, so it cannot become tool failover.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from warnetech_core.agi.orchestrator import AGIOrchestrator
from warnetech_core.agi.planning import Goal
from warnetech_core.agi.tool_metrics import ToolMetrics
from warnetech_core.agi.tools import ToolDefinition, ToolRegistry

REASON_OK = '{"step": 1, "thought": "think", "confidence": 0.9}\n{"conclusion": "answer"}'
REFLECT_RETRY = '{"critiques": [], "overall_quality": 0.4, "should_retry": true}'
REFLECT_PASS = '{"critiques": [], "overall_quality": 0.9, "should_retry": false}'


class _Gateway:
    """Hint plan; reflection always asks for a retry, so the orchestrator keeps retrying."""

    def __init__(self, reflection: str = REFLECT_RETRY) -> None:
        self.reflection = reflection

    async def complete(self, model, messages, *, system=None, **_kw):
        system = system or ""
        if "task planner" in system:
            text = json.dumps([{"title": "t", "description": "d", "tool_hint": "hint"}])
        elif "reasoning engine" in system:
            text = REASON_OK
        else:
            text = self.reflection
        return SimpleNamespace(text=text)


def _registry(executed: list[str], *, alt_fails: bool, hint_fails: bool) -> ToolRegistry:
    registry = ToolRegistry()

    async def hint_handler(**_kwargs):
        executed.append("hint")
        if hint_fails:
            raise RuntimeError("hint failed")
        return "ok"

    async def alt_handler(**_kwargs):
        executed.append("alt")
        if alt_fails:
            raise RuntimeError("alt failed")
        return "ok"

    registry.register(ToolDefinition(name="hint", selection_group="g"), handler=hint_handler)
    registry.register(ToolDefinition(name="alt", selection_group="g"), handler=alt_handler)
    return registry


def _seed(state, outcomes: dict[str, tuple[int, int]]) -> None:
    metrics = ToolMetrics()
    for name, (wins, losses) in outcomes.items():
        for _ in range(wins):
            metrics.record(name, success=True)
        for _ in range(losses):
            metrics.record(name, success=False)
    state.mkdir(parents=True, exist_ok=True)
    metrics.save(state / "tool_metrics.json")


# PB4 F008 regression. Metrics make "alt" the initial winner (alt 3/0 = 0.80 vs hint 4/1 = 0.71... 
# hint 3/1 would tie; here hint is 4/1 = 0.714 < alt 0.80, so alt is selected first).
# alt then fails, so its rate drops to 3/1 = 0.667, below hint's 0.714. A fresh ranking would
# now prefer "hint". Under the approved policy the retry must still run "alt".


async def test_retry_keeps_metric_selected_tool_after_failure(tmp_path):
    state = tmp_path / "state"
    _seed(state, {"alt": (3, 0), "hint": (4, 1)})
    executed: list[str] = []
    orch = AGIOrchestrator(
        _Gateway(),  # type: ignore[arg-type]
        tool_registry=_registry(executed, alt_fails=True, hint_fails=False),
        enable_phase2=True,
        state_dir=state,
        enable_metric_selection=True,
    )

    await orch.run(Goal(id="pb4-1", description="x"))

    assert executed[0] == "alt"
    assert executed[1] == "alt", f"retry reselected a tool: {executed}"
    assert "hint" not in executed, f"retry fell back to the hint: {executed}"


async def test_changed_metrics_between_attempts_do_not_switch_tool(tmp_path):
    state = tmp_path / "state"
    _seed(state, {"alt": (3, 0), "hint": (4, 1)})
    executed: list[str] = []
    orch = AGIOrchestrator(
        _Gateway(),  # type: ignore[arg-type]
        tool_registry=_registry(executed, alt_fails=True, hint_fails=False),
        enable_phase2=True,
        state_dir=state,
        enable_metric_selection=True,
    )

    await orch.run(Goal(id="pb4-2", description="x"))

    # After the first failure, a fresh ranking prefers "hint". Retry must not act on that.
    fresh = ToolMetrics.load(state / "tool_metrics.json")
    assert fresh.posterior_mean("hint") > fresh.posterior_mean("alt")
    assert executed[1] == "alt"


async def test_default_off_retry_repeats_the_hinted_tool(tmp_path):
    executed: list[str] = []
    orch = AGIOrchestrator(
        _Gateway(),  # type: ignore[arg-type]
        tool_registry=_registry(executed, alt_fails=False, hint_fails=True),
    )

    await orch.run(Goal(id="pb4-3", description="x"))

    assert executed
    assert set(executed) == {"hint"}


async def test_retry_count_is_unchanged(tmp_path):
    executed: list[str] = []
    orch = AGIOrchestrator(
        _Gateway(),  # type: ignore[arg-type]
        tool_registry=_registry(executed, alt_fails=False, hint_fails=True),
        max_retries_per_task=2,
    )

    await orch.run(Goal(id="pb4-4", description="x"))

    # One initial attempt plus two retries, as before.
    assert len(executed) == 3


async def test_metrics_attribute_retries_to_the_executed_tool(tmp_path):
    state = tmp_path / "state"
    _seed(state, {"alt": (3, 0), "hint": (4, 1)})
    executed: list[str] = []
    orch = AGIOrchestrator(
        _Gateway(),  # type: ignore[arg-type]
        tool_registry=_registry(executed, alt_fails=True, hint_fails=False),
        enable_phase2=True,
        state_dir=state,
        enable_metric_selection=True,
    )

    await orch.run(Goal(id="pb4-5", description="x"))

    reloaded = ToolMetrics.load(state / "tool_metrics.json")
    assert reloaded.stats("hint").failures == 1  # only the seeded failure
    assert reloaded.stats("hint").successes == 4  # unchanged: hint never ran
    assert reloaded.stats("alt").failures == len(executed)  # every attempt was alt
