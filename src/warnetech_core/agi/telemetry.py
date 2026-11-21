"""Opt-in JSONL telemetry for AGI runs (Phase 1, T5).

Each event is one JSON object per line with ``goal_id``, ``event``,
``timestamp`` (UTC, ISO 8601) and ``payload``. Write failures are logged and
swallowed so telemetry never breaks a run.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any

log = logging.getLogger("warnetech.agi.telemetry")


class TelemetryEvent(str, Enum):
    GOAL_RECEIVED = "goal_received"
    PLAN_GENERATED = "plan_generated"
    TOOL_SELECTED = "tool_selected"
    EXECUTION_COMPLETED = "execution_completed"
    REFLECTION_COMPLETED = "reflection_completed"
    # Emitted when an opt-in Phase 2 write fails. The run continues. Payload holds
    # only the component name and the exception class, never its message.
    PERSISTENCE_FAILED = "persistence_failed"


class Telemetry:
    """Appends AGI events to a JSONL file. The parent directory is created on first write."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def record(
        self,
        event: TelemetryEvent,
        *,
        goal_id: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        entry = {
            "goal_id": goal_id,
            "event": event.value,
            "timestamp": datetime.now(UTC).isoformat(),
            "payload": payload or {},
        }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, sort_keys=True) + "\n")
        except OSError as exc:
            log.warning("telemetry write failed for %s: %s", self.path, exc)
