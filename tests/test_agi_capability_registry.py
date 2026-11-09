"""Tests for warnetech_core.agi.capability_registry and tool metadata (T3)."""

from __future__ import annotations

import pytest

from warnetech_core.agi.capability_registry import (
    Capability,
    CapabilityRegistry,
    register_builtin_capabilities,
)
from warnetech_core.agi.tools import ToolDefinition


def test_capability_defaults_to_version_one_and_no_dependencies():
    cap = Capability(name="planner", description="goal planner")
    assert cap.version == "1.0.0"
    assert cap.dependencies == []


def test_register_then_get_returns_the_same_capability():
    registry = CapabilityRegistry()
    registry.register(Capability(name="planner", description="goal planner"))
    found = registry.get_capability("planner")
    assert found is not None
    assert found.name == "planner"
    assert found.description == "goal planner"


def test_get_unknown_capability_returns_none():
    assert CapabilityRegistry().get_capability("missing") is None


def test_exists_reflects_registration():
    registry = CapabilityRegistry()
    assert registry.exists("planner") is False
    registry.register(Capability(name="planner", description="goal planner"))
    assert registry.exists("planner") is True


def test_list_capabilities_is_sorted_by_name():
    registry = CapabilityRegistry()
    registry.register(Capability(name="reflector", description=""))
    registry.register(Capability(name="planner", description=""))
    names = [c.name for c in registry.list_capabilities()]
    assert names == ["planner", "reflector"]


def test_duplicate_registration_is_rejected_by_default():
    registry = CapabilityRegistry()
    registry.register(Capability(name="planner", description="v1"))
    with pytest.raises(ValueError, match="already registered: planner"):
        registry.register(Capability(name="planner", description="v2"))
    assert registry.get_capability("planner").description == "v1"


def test_replace_true_overwrites_existing_capability():
    registry = CapabilityRegistry()
    registry.register(Capability(name="planner", description="v1"))
    registry.register(Capability(name="planner", description="v2"), replace=True)
    assert registry.get_capability("planner").description == "v2"


def test_validate_dependencies_reports_unknown_dependency():
    registry = CapabilityRegistry()
    registry.register(Capability(name="orchestrator", description="", dependencies=["planner"]))
    problems = registry.validate_dependencies()
    assert problems == ["orchestrator depends on unknown capability planner"]


def test_validate_dependencies_reports_cycle():
    registry = CapabilityRegistry()
    registry.register(Capability(name="a", description="", dependencies=["b"]))
    registry.register(Capability(name="b", description="", dependencies=["a"]))
    problems = registry.validate_dependencies()
    assert problems, "a two-node cycle must be reported"
    assert any("cycle" in p and "a" in p and "b" in p for p in problems)


def test_valid_dependency_graph_has_no_problems():
    registry = CapabilityRegistry()
    registry.register(Capability(name="planner", description=""))
    registry.register(Capability(name="orchestrator", description="", dependencies=["planner"]))
    assert registry.validate_dependencies() == []


def test_builtin_capabilities_declare_real_dependencies():
    registry = CapabilityRegistry()
    register_builtin_capabilities(registry)
    names = [c.name for c in registry.list_capabilities()]
    assert names == ["orchestrator", "planner", "reasoner", "reflector"]
    assert sorted(registry.get_capability("orchestrator").dependencies) == [
        "planner",
        "reasoner",
        "reflector",
    ]
    assert registry.validate_dependencies() == []


def test_tool_definition_has_version_and_dependencies_defaults():
    tool = ToolDefinition(name="read_file", description="read a file")
    assert tool.version == "1.0.0"
    assert tool.dependencies == []
