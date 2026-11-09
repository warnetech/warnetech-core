"""P2-4: schema_version 1 on every Phase 2 state file, and reflection restart recovery."""

from __future__ import annotations

import json

import pytest

from warnetech_core.agi.state import AgentStateStore, ReflectionHistory, StateError
from warnetech_core.agi.tool_metrics import ToolMetrics


def _doc(path):
    return json.loads(path.read_text(encoding="utf-8"))


# --- schema_version: 1 is written -------------------------------------------


def test_goals_file_is_written_with_schema_version_1(tmp_path):
    AgentStateStore(tmp_path).record_goal("g1", "x", "done")
    doc = _doc(tmp_path / "goals.json")
    assert doc["schema_version"] == 1
    assert doc["records"][0]["goal_id"] == "g1"


def test_reflections_file_is_written_with_schema_version_1(tmp_path):
    ReflectionHistory(tmp_path).record(
        "g1", overall_quality=0.5, should_retry=False, critique_count=0
    )
    doc = _doc(tmp_path / "reflections.json")
    assert doc["schema_version"] == 1
    assert doc["records"][0]["goal_id"] == "g1"


def test_tool_metrics_file_is_written_with_schema_version_1(tmp_path):
    metrics = ToolMetrics()
    metrics.record("lookup", success=True)
    metrics.save(tmp_path / "tool_metrics.json")
    doc = _doc(tmp_path / "tool_metrics.json")
    assert doc["schema_version"] == 1
    assert doc["tools"]["lookup"]["successes"] == 1


# --- version-aware loads -----------------------------------------------------


def test_unversioned_goals_file_is_rejected(tmp_path):
    # Migration notes: no unversioned production data exists, so a file without
    # schema_version is not silently treated as version 1.
    (tmp_path / "goals.json").write_text("[]", encoding="utf-8")
    with pytest.raises(StateError):
        AgentStateStore(tmp_path).goal_history("g1")


def test_unsupported_schema_version_is_rejected(tmp_path):
    (tmp_path / "reflections.json").write_text(
        json.dumps({"schema_version": 2, "records": []}), encoding="utf-8"
    )
    with pytest.raises(StateError):
        ReflectionHistory(tmp_path).history()


def test_unsupported_schema_version_is_rejected_for_tool_metrics(tmp_path):
    (tmp_path / "tool_metrics.json").write_text(
        json.dumps({"schema_version": 2, "tools": {}}), encoding="utf-8"
    )
    with pytest.raises(StateError):
        ToolMetrics.load(tmp_path / "tool_metrics.json")


def test_unversioned_tool_metrics_file_is_rejected(tmp_path):
    (tmp_path / "tool_metrics.json").write_text(
        json.dumps({"lookup": {"successes": 1, "failures": 0, "total_latency_ms": 0}}),
        encoding="utf-8",
    )
    with pytest.raises(StateError):
        ToolMetrics.load(tmp_path / "tool_metrics.json")


# --- reflection restart recovery --------------------------------------------


def test_reflections_reload_through_a_new_instance(tmp_path):
    first = ReflectionHistory(tmp_path)
    first.record("g1", overall_quality=0.4, should_retry=True, critique_count=2)
    first.record("g1", overall_quality=0.8, should_retry=False, critique_count=0)
    expected = first.history("g1")
    del first  # discard the writer; the next read must come from disk alone

    second = ReflectionHistory(tmp_path)
    assert second.history("g1") == expected
    assert second.mean_quality("g1") == pytest.approx(0.6)
