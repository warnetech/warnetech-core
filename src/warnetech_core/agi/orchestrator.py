"""The AGI orchestrator — the main loop tying reasoning, planning, tools and reflection.

Runs a goal through: plan → reason → act → reflect → repeat.  Each cycle
either advances the plan or revises it based on reflection feedback.  The
loop terminates when the plan is complete, a retry limit is hit, or the
caller cancels.
"""

from __future__ import annotations

import logging
import time

from pydantic import BaseModel, Field

from ..gateway import AIGateway
from ..memory.service import MemoryService
from .planning import Goal, Plan, Planner, PlanStatus
from .reasoning import ReasoningEngine, ReasoningResult, ReasoningStrategy
from .reflection import ReflectionResult, Reflector
from .tools import ToolCall, ToolRegistry, ToolResult

log = logging.getLogger("warnetech.agi.orchestrator")


class CycleRecord(BaseModel):
    """Trace of one orchestrator cycle."""

    cycle: int
    task_id: str
    task_title: str
    reasoning: ReasoningResult | None = None
    tool_results: list[ToolResult] = Field(default_factory=list)
    reflection: ReflectionResult | None = None
    outcome: str = ""
    elapsed_ms: float = 0.0


class OrchestratorResult(BaseModel):
    """Final result of an orchestrated AGI run."""

    goal: Goal
    plan: Plan
    cycles: list[CycleRecord] = Field(default_factory=list)
    success: bool = False
    summary: str = ""
    total_ms: float = 0.0


class AGIOrchestrator:
    """Autonomous goal-directed agent loop.

    Ties together planning, reasoning, tool execution and self-reflection
    into an iterative cycle that works toward a goal.

    Example::

        orchestrator = AGIOrchestrator(gateway, memory)
        result = await orchestrator.run(Goal(
            description="Analyse the sales data and produce a summary report",
            success_criteria=["Report covers Q3 trends", "Includes top 5 products"],
        ))
        print(result.summary)
        print(f"Completed in {len(result.cycles)} cycles")
    """

    def __init__(
        self,
        gateway: AIGateway,
        memory: MemoryService | None = None,
        *,
        tool_registry: ToolRegistry | None = None,
        model: str = "chat.default",
        project: str = "agi",
        max_cycles: int = 20,
        max_retries_per_task: int = 2,
    ) -> None:
        self.gateway = gateway
        self.memory = memory
        self.planner = Planner(gateway, model=model, project=project)
        self.reasoner = ReasoningEngine(gateway, model=model, project=project)
        self.reflector = Reflector(gateway, model=model, project=project)
        self.tools = tool_registry or ToolRegistry()
        self.model = model
        self.project = project
        self.max_cycles = max_cycles
        self.max_retries_per_task = max_retries_per_task

    async def run(self, goal: Goal) -> OrchestratorResult:
        """Execute the full AGI loop for *goal*."""
        started = time.perf_counter()
        cycles: list[CycleRecord] = []

        plan = await self.planner.decompose(goal)
        if not plan.tasks:
            return OrchestratorResult(
                goal=goal,
                plan=plan,
                success=False,
                summary="Planner produced no tasks",
                total_ms=(time.perf_counter() - started) * 1000,
            )

        plan.status = PlanStatus.IN_PROGRESS
        retries: dict[str, int] = {}

        for cycle_num in range(1, self.max_cycles + 1):
            ready = plan.ready_tasks()
            if not ready:
                if plan.is_complete():
                    break
                if plan.is_failed():
                    break
                log.warning("no ready tasks but plan not complete — deadlock?")
                break

            task = ready[0]
            task.status = PlanStatus.IN_PROGRESS
            cycle_started = time.perf_counter()

            record = CycleRecord(
                cycle=cycle_num,
                task_id=task.id,
                task_title=task.title,
            )

            context = ""
            if self.memory:
                try:
                    ctx = await self.memory.get_context(self.project, task.title)
                    context = ctx.text
                except Exception as exc:
                    log.debug("memory lookup failed: %s", exc)

            reasoning = await self.reasoner.reason(
                f"How should I accomplish this task: {task.title}\n\n{task.description}",
                context=context,
                strategy=ReasoningStrategy.CHAIN_OF_THOUGHT,
            )
            record.reasoning = reasoning

            if task.tool_hint and self.tools.get(task.tool_hint):
                tool_call = ToolCall(
                    tool_name=task.tool_hint,
                    arguments={"task": task.title, "description": task.description},
                )
                tool_result = await self.tools.execute(tool_call)
                record.tool_results.append(tool_result)

            output_text = reasoning.conclusion
            if record.tool_results:
                last_tool = record.tool_results[-1]
                if last_tool.success and last_tool.output:
                    output_text += f"\n\nTool output: {last_tool.output}"

            reflection = await self.reflector.evaluate(
                question=task.title,
                output=output_text,
                criteria=goal.success_criteria,
            )
            record.reflection = reflection

            if reflection.should_retry and retries.get(task.id, 0) < self.max_retries_per_task:
                retries[task.id] = retries.get(task.id, 0) + 1
                task.status = PlanStatus.PENDING
                record.outcome = "retry"
                log.info("retrying task %s (attempt %d)", task.title, retries[task.id])
            elif reflection.has_errors:
                task.mark_failed(reflection.revised_output or "reflection found critical errors")
                record.outcome = "failed"
            else:
                final_output = reflection.revised_output or output_text
                task.mark_complete(final_output)
                record.outcome = "completed"

                if self.memory:
                    try:
                        await self.memory.remember(
                            self.project,
                            f"Task completed: {task.title}\nResult: {final_output}",
                            key=f"agi-task-{task.id}",
                        )
                    except Exception as exc:
                        log.debug("memory store failed: %s", exc)

            record.elapsed_ms = (time.perf_counter() - cycle_started) * 1000
            cycles.append(record)

        total_ms = (time.perf_counter() - started) * 1000
        success = plan.is_complete()
        plan.status = PlanStatus.COMPLETED if success else PlanStatus.FAILED

        summary = self._build_summary(plan, cycles, success)

        return OrchestratorResult(
            goal=goal,
            plan=plan,
            cycles=cycles,
            success=success,
            summary=summary,
            total_ms=total_ms,
        )

    def _build_summary(self, plan: Plan, cycles: list[CycleRecord], success: bool) -> str:
        completed = [t for t in plan.tasks if t.status == PlanStatus.COMPLETED]
        failed = [t for t in plan.tasks if t.status == PlanStatus.FAILED]
        parts = [
            f"{'Completed' if success else 'Incomplete'}: "
            f"{len(completed)}/{len(plan.tasks)} tasks done"
        ]
        if failed:
            parts.append(f"Failed: {', '.join(t.title for t in failed)}")
        parts.append(f"Cycles: {len(cycles)}")
        return ". ".join(parts)
