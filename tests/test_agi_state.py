"""Tests for warnetech_core.agi.state (Phase 2: persistent state, reflection history)."""

from __future__ import annotations

import json

import pytest

from warnetech_core.agi.state import AgentStateStore, ReflectionHistory, StateError


def test_goal_history_is_ordered_and_filtered_by_goal(tmp_path):
    store = AgentStateStore(tmp_path)
    store.record_goal("g1", "reconstruct", "pending")
    store.record_goal("g2", "other", "pending")
    store.record_goal("g1", "reconstruct", "in_progress")
    store.record_goal("g1", "reconstruct", "done")

    history = store.goal_history("g1")
    assert [r.status for r in history] == ["pending", "in_progress", "done"]
    assert store.latest_status("g1") == "done"
    assert store.latest_status("g2") == "pending"


def test_unknown_goal_has_empty_history_and_no_status(tmp_path):
    store = AgentStateStore(tmp_path)
    assert store.goal_history("missing") == []
    assert store.latest_status("missing") is None


def test_goal_history_survives_a_new_store_instance(tmp_path):
    AgentStateStore(tmp_path).record_goal("g1", "x", "done")
    assert AgentStateStore(tmp_path).latest_status("g1") == "done"


def test_corrupt_goal_file_raises_state_error(tmp_path):
    (tmp_path / "goals.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(StateError):
        AgentStateStore(tmp_path).goal_history("g1")


def test_goal_file_must_hold_a_list(tmp_path):
    (tmp_path / "goals.json").write_text(json.dumps({"a": 1}), encoding="utf-8")
    with pytest.raises(StateError):
        AgentStateStore(tmp_path).goal_history("g1")


def test_write_leaves_no_temp_files_behind(tmp_path):
    AgentStateStore(tmp_path).record_goal("g1", "x", "done")
    names = sorted(p.name for p in tmp_path.iterdir())
    assert names == ["goals.json"]


def test_reflection_history_records_and_filters(tmp_path):
    history = ReflectionHistory(tmp_path)
    history.record("g1", overall_quality=0.4, should_retry=True, critique_count=2)
    history.record("g2", overall_quality=0.9, should_retry=False, critique_count=0)
    history.record("g1", overall_quality=0.8, should_retry=False, critique_count=1)

    assert len(history.history()) == 3
    g1 = history.history("g1")
    assert [r.overall_quality for r in g1] == [0.4, 0.8]
    assert history.mean_quality("g1") == pytest.approx(0.6)
    assert history.mean_quality() == pytest.approx((0.4 + 0.9 + 0.8) / 3)


def test_reflection_mean_is_none_when_empty(tmp_path):
    assert ReflectionHistory(tmp_path).mean_quality() is None


def test_reflection_rejects_quality_out_of_range(tmp_path):
    with pytest.raises(ValueError):
        ReflectionHistory(tmp_path).record(
            "g1", overall_quality=1.5, should_retry=False, critique_count=0
        )


def test_reflection_rejects_negative_critique_count(tmp_path):
    with pytest.raises(ValueError):
        ReflectionHistory(tmp_path).record(
            "g1", overall_quality=0.5, should_retry=False, critique_count=-1
        )
