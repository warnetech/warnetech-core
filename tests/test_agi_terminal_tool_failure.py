"""PB-6: a terminal tool failure cannot produce a successful run.

Controls (recovered failure, no new retry, identity, no failover, exception-message
exclusion) should already be green. The red tests are the ones that need the change.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from warnetech_core.agi.orchestrator import AGIOrchestrator
from warnetech_core.agi.planning import Goal
from warnetech_core.agi.telemetry import Telemetry
from warnetech_core.agi.tool_metrics import ToolMetrics
from warnetech_core.agi.tools import ToolDefinition, ToolRegistry

REASON_OK = '{"step": 1, "thought": "think", "confidence": 0.9}\n{"conclusion": "answer"}'
CLEAN = '{"critiques": [], "overall_quality": 0.9, "should_retry": false}'
RETRY = '{"critiques": [], "overall_quality": 0.4, "should_retry": true}'
SENTINEL = "SECRET_SENTINEL_PATH_OR_TOKEN"


class _Gateway:
    """Plans a hinted task. Reflection replies come from `replies`, last one repeating."""

    def __init__(self, replies: list[str], plan_hint: str = "hint") -> None:
        self.replies = list(replies)
        self.plan_hint = plan_hint
        self.reflect_inputs: list[str] = []

    async def complete(self, model, messages, *, system=None, **_kw):
        system = system or ""
        if "task planner" in system:
            text = json.dumps([{"title": "t", "description": "d", "tool_hint": self.plan_hint}])
        elif "reasoning engine" in system:
            text = REASON_OK
        else:
            self.reflect_inputs.append(json.dumps(messages, default=str))
            text = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        return SimpleNamespace(text=text)


def _tool_registry(executed: list[str], handlers: dict) -> ToolRegistry:
    registry = ToolRegistry()
    for name, handler in handlers.items():
        async def wrapped(_name=name, _handler=handler, **kwargs):
            executed.append(_name)
            return await _handler(**kwargs)

        registry.register(ToolDefinition(name=name, selection_group="g"), handler=wrapped)
    return registry


async def _always_raise(**_kwargs):
    raise RuntimeError(SENTINEL)


async def _ok(**_kwargs):
    return "ok"


# RED: today a clean reflection lets a terminal tool failure report success=True.


async def test_terminal_tool_failure_is_not_success():
    executed: list[str] = []
    gw = _Gateway([CLEAN])
    orch = AGIOrchestrator(
        gw,  # type: ignore[arg-type]
        tool_registry=_tool_registry(executed, {"hint": _always_raise}),
    )

    result = await orch.run(Goal(id="tf-1", description="x"))

    assert [c.outcome for c in result.cycles] == ["failed"]
    assert result.success is False


# RED: reflection today never sees the tool failure.


async def test_reflection_receives_bounded_failure_context():
    executed: list[str] = []
    gw = _Gateway([CLEAN])
    orch = AGIOrchestrator(
        gw,  # type: ignore[arg-type]
        tool_registry=_tool_registry(executed, {"hint": _always_raise}),
    )

    await orch.run(Goal(id="tf-2", description="x"))

    joined = "\n".join(gw.reflect_inputs)
    assert "Tool failed: hint (RuntimeError)" in joined


# GUARD: the exception message must never reach reflection or telemetry.


async def test_exception_message_is_excluded(tmp_path):
    executed: list[str] = []
    gw = _Gateway([CLEAN])
    telemetry_path = tmp_path / "events.jsonl"
    orch = AGIOrchestrator(
        gw,  # type: ignore[arg-type]
        tool_registry=_tool_registry(executed, {"hint": _always_raise}),
        telemetry=Telemetry(telemetry_path),
    )

    await orch.run(Goal(id="tf-3", description="x"))

    assert SENTINEL not in "\n".join(gw.reflect_inputs)
    assert SENTINEL not in telemetry_path.read_text(encoding="utf-8")


# GUARD (D1): a failed attempt followed by an approved successful retry may succeed.


async def test_recovered_failure_can_succeed():
    attempts = {"n": 0}
    executed: list[str] = []

    async def flaky(**_kwargs):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise RuntimeError("transient")
        return "ok"

    gw = _Gateway([RETRY, CLEAN])
    orch = AGIOrchestrator(
        gw,  # type: ignore[arg-type]
        tool_registry=_tool_registry(executed, {"hint": flaky}),
    )

    result = await orch.run(Goal(id="tf-4", description="x"))

    assert executed == ["hint", "hint"]
    assert result.success is True
    assert [c.outcome for c in result.cycles][-1] == "completed"


# GUARD (D2): a terminal failure with no retry requested does not create another attempt.


async def test_terminal_failure_creates_no_new_attempt():
    executed: list[str] = []
    gw = _Gateway([CLEAN])
    orch = AGIOrchestrator(
        gw,  # type: ignore[arg-type]
        tool_registry=_tool_registry(executed, {"hint": _always_raise}),
    )

    await orch.run(Goal(id="tf-5", description="x"))

    assert executed == ["hint"]


# RED (identity): reflection names the tool that actually failed, not the planner hint.


async def test_reflection_names_the_executed_tool(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    metrics = ToolMetrics()
    for _ in range(3):
        metrics.record("alt", success=True)
    metrics.record("hint", success=False)
    metrics.save(state / "tool_metrics.json")

    executed: list[str] = []
    gw = _Gateway([CLEAN])
    orch = AGIOrchestrator(
        gw,  # type: ignore[arg-type]
        tool_registry=_tool_registry(executed, {"hint": _ok, "alt": _always_raise}),
        enable_phase2=True,
        state_dir=state,
        enable_metric_selection=True,
    )

    result = await orch.run(Goal(id="tf-6", description="x"))

    assert executed == ["alt"]
    joined = "\n".join(gw.reflect_inputs)
    assert "Tool failed: alt (RuntimeError)" in joined
    assert "Tool failed: hint" not in joined
    assert result.success is False


# GUARD (no failover): the failed selected tool is never replaced by another candidate.


async def test_terminal_failure_does_not_fail_over(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    metrics = ToolMetrics()
    for _ in range(3):
        metrics.record("alt", success=True)
    metrics.save(state / "tool_metrics.json")

    executed: list[str] = []
    gw = _Gateway([CLEAN])
    orch = AGIOrchestrator(
        gw,  # type: ignore[arg-type]
        tool_registry=_tool_registry(executed, {"hint": _ok, "alt": _always_raise}),
        enable_phase2=True,
        state_dir=state,
        enable_metric_selection=True,
    )

    await orch.run(Goal(id="tf-7", description="x"))

    assert "hint" not in executed


# RED (D3): the existing execution_completed.success reports the terminal failure.


async def test_execution_completed_reports_terminal_failure(tmp_path):
    executed: list[str] = []
    gw = _Gateway([CLEAN])
    telemetry_path = tmp_path / "events.jsonl"
    orch = AGIOrchestrator(
        gw,  # type: ignore[arg-type]
        tool_registry=_tool_registry(executed, {"hint": _always_raise}),
        telemetry=Telemetry(telemetry_path),
    )

    await orch.run(Goal(id="tf-8", description="x"))

    events = [json.loads(line) for line in telemetry_path.read_text(encoding="utf-8").splitlines()]
    done = [e for e in events if e["event"] == "execution_completed"]
    assert len(done) == 1
    assert done[0]["payload"]["success"] is False


# GUARD (plan B1): a recovered task's reflection never receives terminal-failure context.


async def test_successful_retry_is_not_marked_as_terminal_tool_failure():
    attempts = {"n": 0}

    async def flaky(**_kwargs):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise RuntimeError("transient")
        return "ok"

    gw = _Gateway([RETRY, CLEAN])
    orch = AGIOrchestrator(
        gw,  # type: ignore[arg-type]
        tool_registry=_tool_registry([], {"hint": flaky}),
    )

    await orch.run(Goal(id="tf-9", description="x"))

    assert gw.reflect_inputs
    assert not any("Tool failed:" in text for text in gw.reflect_inputs)


# GUARD (plan E2): a terminal failure adds exactly one ToolMetrics failure, the attempt's own.


async def test_terminal_failure_does_not_duplicate_tool_metrics(tmp_path):
    state = tmp_path / "state"
    gw = _Gateway([CLEAN])
    orch = AGIOrchestrator(
        gw,  # type: ignore[arg-type]
        tool_registry=_tool_registry([], {"hint": _always_raise}),
        enable_phase2=True,
        state_dir=state,
    )

    await orch.run(Goal(id="tf-10", description="x"))

    reloaded = ToolMetrics.load(state / "tool_metrics.json")
    assert reloaded.stats("hint").failures == 1
    assert reloaded.stats("hint").successes == 0
