"""PB-1: tool_selected telemetry explains the completed selection. It never changes it."""

from __future__ import annotations

import json
from types import SimpleNamespace

from warnetech_core.agi.candidates import resolve_candidates
from warnetech_core.agi.orchestrator import AGIOrchestrator
from warnetech_core.agi.planning import Goal
from warnetech_core.agi.telemetry import Telemetry
from warnetech_core.agi.tool_metrics import ToolMetrics
from warnetech_core.agi.tools import ToolDefinition, ToolRegistry

REASON_OK = '{"step": 1, "thought": "think", "confidence": 0.9}\n{"conclusion": "answer"}'
REFLECT_PASS = '{"critiques": [], "overall_quality": 0.9, "should_retry": false}'
REASON_CODES = {"hint_default", "single_candidate", "metric_ranked"}


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


def _seed(state, outcomes: dict[str, tuple[int, int]]) -> None:
    metrics = ToolMetrics()
    for name, (wins, losses) in outcomes.items():
        for _ in range(wins):
            metrics.record(name, success=True)
        for _ in range(losses):
            metrics.record(name, success=False)
    state.mkdir(parents=True, exist_ok=True)
    metrics.save(state / "tool_metrics.json")


def _selected_events(path) -> list[dict]:
    events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    return [e for e in events if e["event"] == "tool_selected"]


def _run_events(tmp_path, *, tools, outcomes, hint, metric=True, state_name="state"):
    state = tmp_path / state_name
    if outcomes is not None:
        _seed(state, outcomes)
    executed: list[str] = []
    registry = _registry(executed, *tools)
    telemetry_path = tmp_path / f"events-{state_name}.jsonl"
    kwargs = {"enable_phase2": True, "state_dir": state}
    if metric:
        kwargs["enable_metric_selection"] = True
    orch = AGIOrchestrator(
        _Gateway(_plan(hint)),  # type: ignore[arg-type]
        tool_registry=registry,
        telemetry=Telemetry(telemetry_path),
        **kwargs,
    )
    return orch, telemetry_path, executed


# PB1-AC1 and PB1-AC18: default payload is unchanged, and no extra event appears.


async def test_default_payload_keeps_baseline_keys(tmp_path):
    orch, path, executed = _run_events(
        tmp_path,
        tools=[ToolDefinition(name="hint"), ToolDefinition(name="alt", selection_group="g")],
        outcomes=None,
        hint="hint",
        metric=False,
    )
    await orch.run(Goal(id="d1", description="x"))

    events = _selected_events(path)
    assert len(events) == 1
    assert set(events[0]["payload"]) == {"cycle", "task_id", "tool_name"}
    assert events[0]["payload"]["tool_name"] == "hint"


# PB1-AC2, PB1-AC3, PB1-AC4, PB1-AC5, PB1-AC9: metric winner is reported in full.


async def test_metric_winner_payload_reports_hint_candidates_and_winner(tmp_path):
    orch, path, executed = _run_events(
        tmp_path,
        tools=[
            ToolDefinition(name="hint", selection_group="g"),
            ToolDefinition(name="alt", selection_group="g"),
        ],
        outcomes={"hint": (0, 5), "alt": (5, 0)},
        hint="hint",
    )
    await orch.run(Goal(id="m1", description="x"))

    payload = _selected_events(path)[0]["payload"]
    assert executed == ["alt"]
    assert payload["tool_name"] == "alt"
    assert payload["hinted_tool"] == "hint"
    assert payload["candidate_tools"] == ["hint", "alt"]
    assert payload["selection_mode"] == "metrics"
    assert payload["selection_reason"] == "metric_ranked"


# PB1-AC5, PB1-AC6, PB1-AC7: candidates equal the resolver output, in order, with no outsiders.


async def test_candidate_telemetry_equals_resolver_output(tmp_path):
    tools = [
        ToolDefinition(name="hint", selection_group="g"),
        ToolDefinition(name="alt", selection_group="g"),
        ToolDefinition(name="outsider"),  # no group: must never be a candidate
    ]
    orch, path, executed = _run_events(
        tmp_path,
        tools=tools,
        outcomes={"hint": (0, 5), "alt": (5, 0), "outsider": (50, 0)},
        hint="hint",
    )
    expected = [t.name for t in resolve_candidates("hint", orch.tools)]

    await orch.run(Goal(id="c1", description="x"))

    payload = _selected_events(path)[0]["payload"]
    assert payload["candidate_tools"] == expected == ["hint", "alt"]
    assert "outsider" not in payload["candidate_tools"]
    assert payload["tool_name"] != "outsider"


# PB1-AC8: a single candidate reports only itself.


async def test_single_candidate_reports_only_the_hint(tmp_path):
    orch, path, executed = _run_events(
        tmp_path,
        tools=[ToolDefinition(name="hint"), ToolDefinition(name="other", selection_group="g")],
        outcomes={"hint": (0, 5), "other": (9, 0)},
        hint="hint",
    )
    await orch.run(Goal(id="s1", description="x"))

    payload = _selected_events(path)[0]["payload"]
    assert payload["candidate_tools"] == ["hint"]
    assert payload["tool_name"] == "hint"
    assert payload["selection_reason"] == "single_candidate"


# PB1-AC10: a tie keeps the hint, and the candidate list still shows both tools.


async def test_tie_reports_hint_winner_with_full_candidate_list(tmp_path):
    orch, path, executed = _run_events(
        tmp_path,
        tools=[
            ToolDefinition(name="hint", selection_group="g"),
            ToolDefinition(name="alt", selection_group="g"),
        ],
        outcomes={},
        hint="hint",
    )
    await orch.run(Goal(id="t1", description="x"))

    payload = _selected_events(path)[0]["payload"]
    assert payload["tool_name"] == "hint"
    assert payload["candidate_tools"] == ["hint", "alt"]
    assert payload["selection_mode"] == "metrics"


# PB1-AC8 and unknown hint: no tool was selected, so no completed selection event.


async def test_unknown_hint_emits_no_tool_selected(tmp_path):
    orch, path, executed = _run_events(
        tmp_path,
        tools=[ToolDefinition(name="best", selection_group="g")],
        outcomes={"best": (9, 0)},
        hint="missing",
    )
    await orch.run(Goal(id="u1", description="x"))

    assert executed == []
    assert _selected_events(path) == []


# PB1-AC12 and PB1-AC11: bounded values only. Payload holds names, never ToolDefinition objects.


async def test_payload_is_bounded_to_names_and_known_reasons(tmp_path):
    orch, path, executed = _run_events(
        tmp_path,
        tools=[
            ToolDefinition(name="hint", selection_group="g"),
            ToolDefinition(name="alt", selection_group="g"),
        ],
        outcomes={"hint": (0, 5), "alt": (5, 0)},
        hint="hint",
    )
    await orch.run(Goal(id="b1", description="x"))

    payload = _selected_events(path)[0]["payload"]
    assert all(isinstance(name, str) for name in payload["candidate_tools"])
    assert payload["selection_reason"] in REASON_CODES
    assert "score" not in json.dumps(payload).lower()


# PB1-AC13 and PB1-AC14: the winner is unchanged by observability, and metrics are untouched.


async def test_selection_is_read_only_and_leaves_metrics_untouched(tmp_path):
    orch, path, executed = _run_events(
        tmp_path,
        tools=[
            ToolDefinition(name="hint", selection_group="g"),
            ToolDefinition(name="alt", selection_group="g"),
        ],
        outcomes={"hint": (0, 5), "alt": (5, 0)},
        hint="hint",
    )
    from warnetech_core.agi.planning import TaskNode

    task = TaskNode(title="research", description="look", tool_hint="hint")
    before = orch.tool_metrics.to_dict()
    first = orch._select_tool_name(task)
    second = orch._select_tool_name(task)

    assert first[0] == second[0] == "alt"
    assert orch.tool_metrics.to_dict() == before
