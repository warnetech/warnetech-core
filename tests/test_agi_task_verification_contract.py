"""PB-7A RED tests: task verification contract (approved minimal two-kind allowlist).

Written before implementation. Expected to fail until PB-7A is implemented:
the contract module does not exist yet, so collection fails with ImportError.
"""

from __future__ import annotations

import json

import pytest
from warnetech_core.agi.verification import (
    MAX_VERIFICATION_CHECKS,
    TaskVerificationContract,
    VerificationCheck,
    VerificationCheckKind,
)

from warnetech_core.agi.orchestrator import AGIOrchestrator
from warnetech_core.agi.planning import Goal, Planner, TaskNode
from warnetech_core.agi.state import AgentStateStore


def _parse(raw: object):
    planner = Planner.__new__(Planner)
    return planner._parse_tasks(json.dumps(raw))


def _task_with(verification: object) -> dict:
    return {"title": "T", "description": "d", "depends_on": [], "verification": verification}


EXEC = {"kind": "execution_success"}
PRESENT = {"kind": "result_present"}


def test_contract001_legacy_task_node_without_contract_is_valid():
    node = TaskNode(title="legacy")
    assert node.verification is None


def test_contract002_missing_verification_key_parses_to_none():
    tasks = _parse([{"title": "T", "description": "d", "depends_on": []}])
    assert len(tasks) == 1
    assert tasks[0].verification is None


def test_contract003_execution_success_parses():
    tasks = _parse([_task_with({"checks": [EXEC]})])
    assert tasks[0].verification == TaskVerificationContract(
        checks=(VerificationCheck(kind=VerificationCheckKind.EXECUTION_SUCCESS),)
    )


def test_contract004_result_present_parses():
    tasks = _parse([_task_with({"checks": [PRESENT]})])
    assert tasks[0].verification.checks == (
        VerificationCheck(kind=VerificationCheckKind.RESULT_PRESENT),
    )


def test_contract005_both_approved_checks_parse_in_declared_order():
    tasks = _parse([_task_with({"checks": [EXEC, PRESENT]})])
    assert [c.kind for c in tasks[0].verification.checks] == [
        VerificationCheckKind.EXECUTION_SUCCESS,
        VerificationCheckKind.RESULT_PRESENT,
    ]


def test_contract006_duplicate_check_kind_rejected():
    with pytest.raises(ValueError, match="Duplicate verification check kind: execution_success"):
        _parse([_task_with({"checks": [EXEC, EXEC]})])


def test_contract007_third_check_rejected_because_maximum_is_two():
    assert MAX_VERIFICATION_CHECKS == 2
    with pytest.raises(ValueError, match="exceeds maximum of 2 checks"):
        _parse([_task_with({"checks": [EXEC, PRESENT, PRESENT]})])


def test_contract008_unknown_kind_rejected():
    with pytest.raises(ValueError, match="Unknown verification check kind: file_exists"):
        _parse([_task_with({"checks": [{"kind": "file_exists"}]})])


def test_contract009_malformed_explicit_contract_raises_value_error():
    with pytest.raises(ValueError):
        _parse([_task_with({"checks": "execution_success"})])
    with pytest.raises(ValueError):
        _parse([_task_with({"checks": [{}]})])
    with pytest.raises(ValueError):
        _parse([_task_with("execution_success")])


def test_contract010_explicit_empty_contract_is_no_usable_contract_not_pass():
    tasks = _parse([_task_with({"checks": []})])
    assert tasks[0].verification is None


def test_contract011_executable_content_rejected():
    with pytest.raises(ValueError, match="Unknown verification check key: predicate"):
        _parse(
            [_task_with({"checks": [{"kind": "execution_success", "predicate": "lambda r: True"}]})]
        )
    with pytest.raises(ValueError, match="Unknown verification check kind"):
        _parse([_task_with({"checks": [{"kind": "__import__('os').system('x')"}]})])


def test_contract012_tool_hint_unchanged_by_contract():
    raw = [_task_with({"checks": [EXEC]}) | {"tool_hint": "search"}]
    tasks = _parse(raw)
    assert tasks[0].tool_hint == "search"


def test_contract013_dependencies_unchanged_by_contract():
    raw = [
        {"title": "A", "description": "a", "depends_on": []},
        {
            "title": "B",
            "description": "b",
            "depends_on": ["A"],
            "verification": {"checks": [PRESENT]},
        },
    ]
    tasks = _parse(raw)
    assert tasks[1].depends_on == [tasks[0].id]


def test_contract014_legacy_planner_fixture_parses_unchanged():
    tasks = _parse(
        [
            {"title": "A", "description": "do A", "depends_on": []},
            {"title": "B", "description": "do B", "depends_on": ["A"]},
        ]
    )
    assert [t.title for t in tasks] == ["A", "B"]
    assert all(t.verification is None for t in tasks)


@pytest.mark.asyncio
async def test_contract015_goal_not_left_running_after_malformed_contract(tmp_path):
    orch = AGIOrchestrator(
        object(),  # type: ignore[arg-type]
        enable_phase2=True,
        state_dir=tmp_path,
    )

    async def malformed_decompose(goal):
        _parse([_task_with({"checks": [{"kind": "file_exists"}]})])

    orch.planner.decompose = malformed_decompose  # type: ignore[method-assign]

    with pytest.raises(ValueError):
        await orch.run(Goal(id="pb7a-1", description="x"))

    assert AgentStateStore(tmp_path).latest_status("pb7a-1") == "failed"


@pytest.mark.asyncio
async def test_contract016_malformed_contract_value_error_propagates_to_caller(tmp_path):
    orch = AGIOrchestrator(
        object(),  # type: ignore[arg-type]
        enable_phase2=True,
        state_dir=tmp_path,
    )

    async def malformed_decompose(goal):
        _parse([_task_with({"checks": [EXEC, EXEC]})])

    orch.planner.decompose = malformed_decompose  # type: ignore[method-assign]

    with pytest.raises(ValueError, match="Duplicate verification check kind"):
        await orch.run(Goal(id="pb7a-2", description="x"))
