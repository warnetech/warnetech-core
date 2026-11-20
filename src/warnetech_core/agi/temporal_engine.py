"""Time-aware goal management, memory consolidation, and autonomous wake-up cycles.

The :class:`TemporalEngine` manages goal deadlines, coordinates periodic memory
consolidation sweeps, and fires proactive autonomous wake-up cycles wired into
the daemon/automation scheduler for continuous self-improvement.
"""

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("TemporalEngine")


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
    """Manages goals with deadlines, memory consolidation, and autonomous wake-ups."""

    def __init__(self, state: dict[str, Any] | None = None):
        self.state = state or {}
        self.registry: list[Callable[..., Any]] = []
        self.goals: dict[str, GoalDeadline] = {}
        self.consolidation_interval = 3600.0  # 1 hour
        self.last_consolidation = time.time()

    def register_hook(self, func: Callable[..., Any]) -> None:
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
        logger.info(f"Goal added: {goal_id} due at {goal.deadline}")
        return goal

    async def step(self, input_payload: dict[str, Any]) -> dict[str, Any]:
        """Execute temporal step and check for consolidation/wake-up cycles."""
        logger.info(f"Executing temporal step with payload keys: {list(input_payload.keys())}")
        self.state.update(input_payload)

        for hook in self.registry:
            if asyncio.iscoroutinefunction(hook):
                self.state = await hook(self.state)
            else:
                self.state = hook(self.state)

        # Check for overdue goals
        overdue = [g for g in self.goals.values() if g.is_overdue()]
        if overdue:
            self.state["overdue_goals"] = [g.goal_id for g in overdue]
            logger.warning(f"Overdue goals: {[g.goal_id for g in overdue]}")

        # Trigger memory consolidation if interval elapsed
        if time.time() - self.last_consolidation > self.consolidation_interval:
            await self._consolidate_memory()

        return self.state

    async def _consolidate_memory(self) -> None:
        """Periodic sweep to consolidate episodic memories and extract heuristics."""
        logger.info("Starting memory consolidation sweep")
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
        """Schedule proactive autonomous wake-up cycle."""

        async def wakeup_loop():
            while True:
                await asyncio.sleep(interval_seconds)
                logger.info(f"Autonomous wakeup: {wakeup_id}")
                if asyncio.iscoroutinefunction(callback):
                    await callback(self.state)
                else:
                    callback(self.state)

        task = asyncio.create_task(wakeup_loop())
        self.state.setdefault("wakeup_tasks", []).append(wakeup_id)
        logger.info(f"Scheduled autonomous wakeup: {wakeup_id} every {interval_seconds}s")
        return task


if __name__ == "__main__":

    async def sample_hook(state: dict[str, Any]) -> dict[str, Any]:
        state["counter"] = state.get("counter", 0) + 1
        return state

    engine = TemporalEngine()
    engine.register_hook(sample_hook)

    async def main():
        res = await engine.step({"task": "initialize"})
        print("Engine state output:", res)

    asyncio.run(main())
