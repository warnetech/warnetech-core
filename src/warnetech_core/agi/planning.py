"""Goal decomposition and task planning.

Takes a high-level :class:`Goal` and breaks it into a :class:`Plan` — a DAG
of :class:`TaskNode` objects with dependency edges.  The planner uses the
gateway for LLM-assisted decomposition and the memory service for context.
"""

from __future__ import annotations

import json
import logging
import math
import time
import uuid
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from ..gateway import AIGateway
from ._coerce import strip_code_fence

log = logging.getLogger("warnetech.agi.planning")


class PlanStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"


class TaskNode(BaseModel):
    """One unit of work in a plan."""

    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    title: str
    description: str = ""
    status: PlanStatus = PlanStatus.PENDING
    depends_on: list[str] = Field(default_factory=list)
    tool_hint: str | None = None
    estimated_seconds: float | None = None
    result: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def is_ready(self) -> bool:
        return self.status == PlanStatus.PENDING and not self.depends_on

    def mark_complete(self, result: str | None = None) -> None:
        self.status = PlanStatus.COMPLETED
        self.result = result

    def mark_failed(self, reason: str) -> None:
        self.status = PlanStatus.FAILED
        self.result = reason


class Goal(BaseModel):
    """A high-level objective to be decomposed into tasks."""

    description: str
    success_criteria: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    context: str = ""


class Plan(BaseModel):
    """A DAG of tasks that achieves a goal."""

    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    goal: Goal
    tasks: list[TaskNode] = Field(default_factory=list)
    status: PlanStatus = PlanStatus.PENDING
    created_at: float = Field(default_factory=time.time)

    def ready_tasks(self) -> list[TaskNode]:
        """Tasks whose dependencies are all completed."""
        completed_ids = {t.id for t in self.tasks if t.status == PlanStatus.COMPLETED}
        return [
            t
            for t in self.tasks
            if t.status == PlanStatus.PENDING and all(dep in completed_ids for dep in t.depends_on)
        ]

    def is_complete(self) -> bool:
        return all(t.status == PlanStatus.COMPLETED for t in self.tasks)

    def is_failed(self) -> bool:
        return any(t.status == PlanStatus.FAILED for t in self.tasks)

    def progress(self) -> float:
        if not self.tasks:
            return 0.0
        done = sum(1 for t in self.tasks if t.status == PlanStatus.COMPLETED)
        return done / len(self.tasks)


_PLANNER_SYSTEM = (
    "You are a task planner. Given a goal, decompose it into concrete tasks. "
    "Output a JSON array of task objects. Each task has: "
    '"title" (str), "description" (str), "depends_on" (list of task titles '
    "that must finish first, empty list if none), "
    '"tool_hint" (optional str — name of a tool that could help), '
    '"estimated_seconds" (optional number). '
    "Order tasks so dependencies come before dependents. "
    "Output ONLY the JSON array, no markdown fences."
)


class Planner:
    """Decomposes goals into executable plans via the gateway.

    Example::

        planner = Planner(gateway)
        plan = await planner.decompose(Goal(
            description="Set up a CI pipeline for the project",
            success_criteria=["Tests run on every PR", "Lint checks pass"],
        ))
        for task in plan.ready_tasks():
            print(task.title)
    """

    def __init__(
        self,
        gateway: AIGateway,
        *,
        model: str = "chat.default",
        project: str = "agi-planning",
    ) -> None:
        self.gateway = gateway
        self.model = model
        self.project = project

    async def decompose(self, goal: Goal) -> Plan:
        """Break *goal* into a task graph."""
        user_content = f"Goal: {goal.description}"
        if goal.success_criteria:
            user_content += "\n\nSuccess criteria:\n" + "\n".join(
                f"- {c}" for c in goal.success_criteria
            )
        if goal.constraints:
            user_content += "\n\nConstraints:\n" + "\n".join(f"- {c}" for c in goal.constraints)
        if goal.context:
            user_content += f"\n\nContext:\n{goal.context}"

        response = await self.gateway.complete(
            self.model,
            user_content,
            system=_PLANNER_SYSTEM,
            project=self.project,
            max_tokens=2048,
            temperature=0.2,
        )

        tasks = self._parse_tasks(response.text)
        plan = Plan(goal=goal, tasks=tasks)

        if tasks:
            plan.status = PlanStatus.PENDING
        else:
            log.warning("planner produced no tasks for goal: %s", goal.description)

        return plan

    def _parse_tasks(self, text: str) -> list[TaskNode]:
        """Parse the LLM's JSON array into TaskNode objects."""
        text = strip_code_fence(text)

        try:
            raw_tasks = json.loads(text)
        except json.JSONDecodeError:
            log.error("failed to parse planner output as JSON: %s", text[:200])
            return []

        if not isinstance(raw_tasks, list):
            log.error("planner output is not a list: %s", type(raw_tasks))
            return []

        # Keep only object entries, so the nodes and their raw dicts stay index-aligned.
        entries = [raw for raw in raw_tasks if isinstance(raw, dict)]
        nodes: list[TaskNode] = []
        title_to_id: dict[str, str] = {}

        for raw in entries:
            node = TaskNode(
                title=str(raw.get("title") or "untitled"),
                description=str(raw.get("description") or ""),
                tool_hint=raw["tool_hint"] if isinstance(raw.get("tool_hint"), str) else None,
                estimated_seconds=_seconds(raw.get("estimated_seconds")),
            )
            title_to_id[node.title] = node.id
            nodes.append(node)

        for raw, node in zip(entries, nodes, strict=True):
            deps = raw.get("depends_on")
            if not isinstance(deps, list):
                continue
            node.depends_on = [
                title_to_id[d] for d in deps if isinstance(d, str) and d in title_to_id
            ]

        return nodes


def _seconds(value: Any) -> float | None:
    """A non-negative duration, or None when the planner gave something unusable."""
    if isinstance(value, bool):
        return None
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return None
    return seconds if seconds >= 0 and math.isfinite(seconds) else None
