"""Tests for warnetech_core.agi.telemetry (T5)."""

from __future__ import annotations

import json

from warnetech_core.agi.telemetry import Telemetry, TelemetryEvent


def _read_lines(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_canonical_events_are_the_phase_one_events_plus_persistence_failed():
    # persistence_failed was added by Phase 2 (P2-7). Nothing else may be added silently.
    assert [e.value for e in TelemetryEvent] == [
        "goal_received",
        "plan_generated",
        "tool_selected",
        "execution_completed",
        "reflection_completed",
        "persistence_failed",
    ]


def test_record_appends_one_json_line_per_event(tmp_path):
    path = tmp_path / "events.jsonl"
    telemetry = Telemetry(path)
    telemetry.record(TelemetryEvent.GOAL_RECEIVED, goal_id="g1")
    telemetry.record(TelemetryEvent.PLAN_GENERATED, goal_id="g1", payload={"tasks": 3})
    lines = _read_lines(path)
    assert [line["event"] for line in lines] == ["goal_received", "plan_generated"]
    assert lines[1]["payload"] == {"tasks": 3}


def test_every_record_carries_goal_id_event_and_timestamp(tmp_path):
    path = tmp_path / "events.jsonl"
    Telemetry(path).record(TelemetryEvent.EXECUTION_COMPLETED, goal_id="abc123")
    (line,) = _read_lines(path)
    assert line["goal_id"] == "abc123"
    assert line["event"] == "execution_completed"
    assert line["timestamp"].endswith("+00:00")


def test_payload_defaults_to_empty_dict(tmp_path):
    path = tmp_path / "events.jsonl"
    Telemetry(path).record(TelemetryEvent.REFLECTION_COMPLETED, goal_id="g")
    (line,) = _read_lines(path)
    assert line["payload"] == {}


def test_parent_directory_is_created_on_first_write_not_at_construction(tmp_path):
    path = tmp_path / "logs" / "agi_events.jsonl"
    telemetry = Telemetry(path)
    assert not path.parent.exists()
    telemetry.record(TelemetryEvent.GOAL_RECEIVED, goal_id="g")
    assert path.exists()


def test_no_file_is_created_when_nothing_is_recorded(tmp_path):
    path = tmp_path / "events.jsonl"
    Telemetry(path)
    assert not path.exists()


def test_write_failure_is_swallowed(tmp_path):
    blocker = tmp_path / "not_a_dir"
    blocker.write_text("occupied")
    telemetry = Telemetry(blocker / "events.jsonl")
    telemetry.record(TelemetryEvent.GOAL_RECEIVED, goal_id="g")  # must not raise
