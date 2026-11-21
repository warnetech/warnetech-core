"""Time-aware goal management, memory consolidation, and autonomous wake-up cycles.

The :class:`TemporalEngine` carries one shared state dict across steps. Each
:meth:`TemporalEngine.step` merges the input into the state, then runs the
registered hooks in order. A hook may return the new state, or return ``None``
after changing the dict in place. Hooks may be sync or async.

On top of that, the engine tracks goal deadlines, runs periodic memory
consolidation sweeps, and schedules autonomous wake-up loops for the
daemon/automation scheduler.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger("warnetech.agi.temporal_engine")
logger = log  # kept for existing callers that import ``logger``

Hook = Callable[[dict[str, Any]], Any]


@dataclass
class GoalDeadline:
    """Time-aware goal with deadline."""

    goal_id: str
    description: str
    deadline: float  # Unix timestamp
    priority: int = 0
    tags: list[str] = field(default_factory=list)

    def is_overdue(self) -> bool:
        return time.time() > self.deadline


class TemporalEngine:
    """Carry state across steps, run hooks, and track goal deadlines.

    Example::

        engine = TemporalEngine()
        engine.register_hook(lambda s: {**s, "count": s.get("count", 0) + 1})
        state = await engine.step({"task": "start"})
    """

    def __init__(self, state: dict[str, Any] | None = None) -> None:
        self.state: dict[str, Any] = dict(state or {})
        self.registry: list[Hook] = []
        self.goals: dict[str, GoalDeadline] = {}
        self.consolidation_interval = 3600.0  # 1 hour
        self.last_consolidation = time.time()

    def register_hook(self, func: Hook) -> None:
        """Register a hook to execute during steps."""
        self.registry.append(func)

    def add_goal_with_deadline(
        self,
        goal_id: str,
        description: str,
        deadline_seconds: float,
        priority: int = 0,
        tags: list[str] | None = None,
    ) -> GoalDeadline:
        """Add a time-aware goal with deadline."""
        goal = GoalDeadline(
            goal_id=goal_id,
            description=description,
            deadline=time.time() + deadline_seconds,
            priority=priority,
            tags=tags or [],
        )
        self.goals[goal_id] = goal
        log.info("Goal added: %s due at %s", goal_id, goal.deadline)
        return goal

    async def step(self, input_payload: dict[str, Any]) -> dict[str, Any]:
        """Merge *input_payload*, run every hook once in order, then housekeeping."""
        log.debug("temporal step with payload keys %s", list(input_payload))
        self.state.update(input_payload)

        for hook in self.registry:
            result = hook(self.state)
            if inspect.isawaitable(result):
                result = await result
            if result is None:
                continue  # the hook changed the dict in place
            if not isinstance(result, dict):
                name = getattr(hook, "__name__", repr(hook))
                raise TypeError(
                    f"hook {name!r} must return a dict or None, not {type(result).__name__}"
                )
            self.state = result

        overdue = [g.goal_id for g in self.goals.values() if g.is_overdue()]
        if overdue:
            self.state["overdue_goals"] = overdue
            log.warning("Overdue goals: %s", overdue)

        if time.time() - self.last_consolidation > self.consolidation_interval:
            await self._consolidate_memory()

        return self.state

    async def _consolidate_memory(self) -> None:
        """Periodic sweep to consolidate episodic memories and extract heuristics."""
        log.info("Starting memory consolidation sweep")
        self.last_consolidation = time.time()
        self.state["last_consolidation"] = self.last_consolidation
        # Memory consolidation happens via reflection.TrajectoryScorer
        self.state["consolidation_triggered"] = True

    async def schedule_autonomous_wakeup(
        self,
        wakeup_id: str,
        interval_seconds: float,
        callback: Callable[..., Any],
    ) -> asyncio.Task[None]:
        """Schedule a proactive autonomous wake-up loop."""

        async def wakeup_loop() -> None:
            while True:
                await asyncio.sleep(interval_seconds)
                log.info("Autonomous wakeup: %s", wakeup_id)
                result = callback(self.state)
                if inspect.isawaitable(result):
                    await result

        task = asyncio.create_task(wakeup_loop())
        self.state.setdefault("wakeup_tasks", []).append(wakeup_id)
        log.info("Scheduled autonomous wakeup: %s every %ss", wakeup_id, interval_seconds)
        return task
