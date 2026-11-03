"""Audit chain, dual control, and deny-by-default authority."""

from __future__ import annotations

import pytest

from llmforge import Agent, FakeProvider, ToolCall, ToolRegistry, tool, tool_response
from llmforge.audit import AuditLog, DualControl, Entry, SafePolicy, audited


@tool
def read_file(path: str) -> str:
    """Read a file.

    Args:
        path: Path to read.
    """
    return "contents"


@tool(destructive=True)
def drop_table(name: str) -> str:
    """Drop a database table.

    Args:
        name: Table name.
    """
    return f"dropped {name}"


# --------------------------------------------------------------------------- #
# chain integrity
# --------------------------------------------------------------------------- #


def test_fresh_chain_verifies():
    log = AuditLog()
    for i in range(5):
        log.record("agent", "step", {"i": i})
    assert log.verify().ok
    assert log.verify().entries == 5


def test_editing_an_entry_breaks_the_chain_at_that_entry():
    log = AuditLog()
    for i in range(4):
        log.record("agent", "step", {"i": i})

    original = log.entries[2]
    log.entries[2] = Entry(
        original.seq, original.at, original.actor, "step", {"i": 99},
        original.prev, original.digest,
    )
    check = log.verify()
    assert not check.ok
    assert check.broken_at == 2
    assert "edited" in check.reason


def test_removing_an_entry_breaks_the_link():
    log = AuditLog()
    for i in range(4):
        log.record("agent", "step", {"i": i})
    del log.entries[1]

    check = log.verify()
    assert not check.ok
    assert "removed, reordered, or inserted" in check.reason


def test_head_changes_on_every_append():
    log = AuditLog()
    heads = [log.head]
    for i in range(3):
        log.record("a", "x", {"i": i})
        heads.append(log.head)
    assert len(set(heads)) == 4


def test_chain_survives_a_round_trip_through_disk(tmp_path):
    path = tmp_path / "audit.jsonl"
    first = AuditLog(path)
    for i in range(3):
        first.record("agent", "step", {"i": i})
    head = first.head

    reloaded = AuditLog(path)
    assert reloaded.verify().ok
    assert reloaded.head == head
    assert len(reloaded.entries) == 3


def test_appending_to_a_reloaded_chain_keeps_it_verifiable(tmp_path):
    path = tmp_path / "audit.jsonl"
    AuditLog(path).record("a", "x", {})
    second = AuditLog(path)
    second.record("b", "y", {})
    assert second.verify().ok
    assert AuditLog(path).verify().ok


def test_secrets_are_redacted_before_they_reach_disk(tmp_path):
    path = tmp_path / "audit.jsonl"
    log = AuditLog(path)
    log.record("agent", "tool.call", {"env": "ANTHROPIC_API_KEY=sk-ant-api03-" + "A" * 30})

    written = path.read_text()
    assert "sk-ant-api03" not in written
    assert "REDACTED" in written
    assert "_redacted" in log.entries[0].detail


# --------------------------------------------------------------------------- #
# dual control
# --------------------------------------------------------------------------- #


def test_one_approver_is_not_enough():
    control = DualControl(AuditLog(), approvers={"alice", "bob"})
    assert control.approve("drop-prod", "alice") is False
    assert not control.authorized("drop-prod")


def test_two_distinct_approvers_authorize():
    control = DualControl(AuditLog(), approvers={"alice", "bob"})
    control.approve("drop-prod", "alice")
    assert control.approve("drop-prod", "bob") is True
    assert control.authorized("drop-prod")


def test_the_same_approver_twice_does_not_satisfy_the_two_person_rule():
    control = DualControl(AuditLog(), approvers={"alice", "bob"})
    control.approve("drop-prod", "alice")
    assert control.approve("drop-prod", "alice") is False


def test_unknown_approver_is_rejected_and_recorded():
    log = AuditLog()
    control = DualControl(log, approvers={"alice", "bob"})
    assert control.approve("drop-prod", "mallory") is False
    assert log.by_action("approval.rejected")


def test_cannot_configure_dual_control_with_too_few_approvers():
    with pytest.raises(ValueError, match="at least 2"):
        DualControl(AuditLog(), approvers={"alice"})


def test_revoking_clears_held_approvals():
    control = DualControl(AuditLog(), approvers={"a", "b"})
    control.approve("x", "a")
    control.approve("x", "b")
    control.revoke("x")
    assert not control.authorized("x")


# --------------------------------------------------------------------------- #
# deny-by-default authority
# --------------------------------------------------------------------------- #


def test_unlisted_tool_is_denied():
    log = AuditLog()
    registry = ToolRegistry(read_file, policy=SafePolicy(log, allow=set()))
    result = registry.dispatch(ToolCall("1", "read_file", {"path": "x"}))
    assert result.is_error and "not in the allowed set" in result.content
    assert log.by_action("tool.denied")


def test_allowed_tool_runs_and_is_recorded():
    log = AuditLog()
    registry = ToolRegistry(read_file, policy=SafePolicy(log, allow={"read_file"}))
    result = registry.dispatch(ToolCall("1", "read_file", {"path": "x"}))
    assert not result.is_error
    assert log.by_action("tool.allowed")


def test_destructive_tool_needs_dual_control_even_when_allowed():
    log = AuditLog()
    control = DualControl(log, approvers={"a", "b"})
    policy = SafePolicy(log, allow={"drop_table"}, dual_control=control)
    registry = ToolRegistry(drop_table, policy=policy)

    denied = registry.dispatch(ToolCall("1", "drop_table", {"name": "users"}))
    assert denied.is_error and "lacks 2 approvals" in denied.content

    control.approve("drop_table", "a")
    control.approve("drop_table", "b")
    assert registry.dispatch(ToolCall("2", "drop_table", {"name": "users"})).content == (
        "dropped users"
    )


def test_on_deny_hook_fires():
    seen: list[str] = []
    policy = SafePolicy(AuditLog(), allow=set(), on_deny=lambda t, c, r: seen.append(c.name))
    ToolRegistry(read_file, policy=policy).dispatch(ToolCall("1", "read_file", {"path": "x"}))
    assert seen == ["read_file"]


# --------------------------------------------------------------------------- #
# agent integration
# --------------------------------------------------------------------------- #


def test_audited_agent_records_every_step_as_it_happens():
    log = AuditLog()
    provider = FakeProvider(
        [tool_response(ToolCall("t", "read_file", {"path": "a"})), "done"]
    )
    agent = audited(
        Agent(provider, tools=ToolRegistry(read_file, policy=SafePolicy(log, allow={"read_file"}))),
        log,
    )
    agent.run("read a")

    steps = log.by_action("step")
    assert len(steps) == 2
    assert steps[0].detail["tools"] == ["read_file"]
    assert log.verify().ok


def test_audit_survives_a_crashed_run():
    """The run you most want a trail for is the one that did not finish."""
    log = AuditLog()

    class Exploding:
        name = "exploding"
        calls = 0

        def complete(self, request):
            self.calls += 1
            if self.calls > 1:
                raise RuntimeError("connection reset")
            return tool_response(ToolCall("t", "read_file", {"path": "a"}))

    agent = audited(
        Agent(Exploding(), tools=ToolRegistry(read_file, policy=SafePolicy(log, allow={"read_file"}))),
        log,
    )
    with pytest.raises(RuntimeError):
        agent.run("go")

    assert log.by_action("step")
    assert log.verify().ok
