"""Selection-group candidate resolution (SG-AC1 to SG-AC8). Metrics are not involved."""

from __future__ import annotations

from warnetech_core.agi.candidates import resolve_candidates
from warnetech_core.agi.tools import ToolDefinition, ToolRegistry


def _registry(*tools: ToolDefinition) -> ToolRegistry:
    registry = ToolRegistry()
    for tool in tools:
        registry.register(tool, handler=lambda **_kw: None)
    return registry


def _names(candidates) -> list[str]:
    return [c.name for c in candidates]


# SG-AC1: default is None, so existing registrations are unaffected.


def test_selection_group_defaults_to_none():
    assert ToolDefinition(name="t").selection_group is None


# SG-AC2: hinted tool without a group is the only candidate.


def test_hint_without_group_resolves_to_itself_only():
    registry = _registry(ToolDefinition(name="a"), ToolDefinition(name="b"))
    assert _names(resolve_candidates("a", registry)) == ["a"]


# SG-AC3 and SG-AC6: same group is a candidate, and the hint stays first.


def test_same_group_tools_are_candidates_with_hint_first():
    registry = _registry(
        ToolDefinition(name="alt", selection_group="file_read"),
        ToolDefinition(name="hint", selection_group="file_read"),
    )
    assert _names(resolve_candidates("hint", registry)) == ["hint", "alt"]


# SG-AC4: a different group is not a candidate.


def test_different_group_is_not_a_candidate():
    registry = _registry(
        ToolDefinition(name="hint", selection_group="file_read"),
        ToolDefinition(name="other", selection_group="file_write"),
    )
    assert _names(resolve_candidates("hint", registry)) == ["hint"]


# SG-AC5: shared tags alone never create a candidate.
# Mirrors the production case: a read tool and a code-execution tool both tagged "interpreter".


def test_shared_tags_do_not_make_tools_candidates():
    registry = _registry(
        ToolDefinition(name="workspace_read", tags=["interpreter", "files"]),
        ToolDefinition(name="run_code", tags=["interpreter", "exec"]),
    )
    assert _names(resolve_candidates("workspace_read", registry)) == ["workspace_read"]


def test_shared_tags_with_one_group_still_need_matching_groups():
    registry = _registry(
        ToolDefinition(name="hint", tags=["interpreter"], selection_group="safe_read"),
        ToolDefinition(name="run_code", tags=["interpreter"], selection_group=None),
    )
    assert _names(resolve_candidates("hint", registry)) == ["hint"]


# SG-AC8: exact matching only, no prefixes or substrings.


def test_group_names_must_match_exactly():
    registry = _registry(
        ToolDefinition(name="hint", selection_group="file_read"),
        ToolDefinition(name="near", selection_group="file_read_advanced"),
    )
    assert _names(resolve_candidates("hint", registry)) == ["hint"]


# SG-AC7: an unknown hint never falls back to the whole registry.


def test_unknown_hint_returns_no_candidates():
    registry = _registry(ToolDefinition(name="a", selection_group="g"))
    assert resolve_candidates("missing", registry) == []


def test_empty_hint_returns_no_candidates():
    registry = _registry(ToolDefinition(name="a", selection_group="g"))
    assert resolve_candidates("", registry) == []
