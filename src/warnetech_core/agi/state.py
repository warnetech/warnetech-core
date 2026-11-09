"""Persistent agent state: goal history and reflection history (Phase 2).

Stored as JSON files under a caller-supplied root directory. Writes are atomic
(temp file + ``os.replace``) so a crash mid-write cannot corrupt a file.
No network, no model calls.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

GOALS_FILE = "goals.json"
REFLECTIONS_FILE = "reflections.json"


class StateError(RuntimeError):
    """Raised when a state file exists but cannot be parsed."""


class GoalRecord(BaseModel):
    """One status transition for a goal."""

    goal_id: str
    description: str
    status: str
    timestamp: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


class ReflectionRecord(BaseModel):
    """Summary of one reflection pass, keyed to a goal."""

    goal_id: str
    overall_quality: float = Field(ge=0.0, le=1.0)
    should_retry: bool
    critique_count: int = Field(ge=0)
    timestamp: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


def _atomic_write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, sort_keys=True)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def _read_list(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise StateError(f"corrupt state file: {path}") from exc
    if not isinstance(data, list):
        raise StateError(f"state file must hold a JSON list: {path}")
    return data


class AgentStateStore:
    """Goal history on disk. One append-only list in ``goals.json``."""

    def __init__(self, root: Path) -> None:
        self._root = Path(root)

    @property
    def root(self) -> Path:
        return self._root

    def record_goal(self, goal_id: str, description: str, status: str) -> GoalRecord:
        record = GoalRecord(goal_id=goal_id, description=description, status=status)
        rows = _read_list(self._root / GOALS_FILE)
        rows.append(record.model_dump())
        _atomic_write_json(self._root / GOALS_FILE, rows)
        return record

    def goal_history(self, goal_id: str) -> list[GoalRecord]:
        """Every recorded transition for ``goal_id``, oldest first."""
        rows = _read_list(self._root / GOALS_FILE)
        return [GoalRecord(**r) for r in rows if r.get("goal_id") == goal_id]

    def latest_status(self, goal_id: str) -> str | None:
        history = self.goal_history(goal_id)
        return history[-1].status if history else None


class ReflectionHistory:
    """Reflection summaries on disk, in ``reflections.json``."""

    def __init__(self, root: Path) -> None:
        self._root = Path(root)

    def record(
        self,
        goal_id: str,
        *,
        overall_quality: float,
        should_retry: bool,
        critique_count: int,
    ) -> ReflectionRecord:
        record = ReflectionRecord(
            goal_id=goal_id,
            overall_quality=overall_quality,
            should_retry=should_retry,
            critique_count=critique_count,
        )
        rows = _read_list(self._root / REFLECTIONS_FILE)
        rows.append(record.model_dump())
        _atomic_write_json(self._root / REFLECTIONS_FILE, rows)
        return record

    def history(self, goal_id: str | None = None) -> list[ReflectionRecord]:
        """Reflections, oldest first. Pass ``goal_id`` to filter to one goal."""
        rows = _read_list(self._root / REFLECTIONS_FILE)
        records = [ReflectionRecord(**r) for r in rows]
        if goal_id is None:
            return records
        return [r for r in records if r.goal_id == goal_id]

    def mean_quality(self, goal_id: str | None = None) -> float | None:
        records = self.history(goal_id)
        if not records:
            return None
        return sum(r.overall_quality for r in records) / len(records)
