"""The AGI orchestrator — the main loop tying reasoning, planning, tools and reflection.

Runs a goal through: plan → reason → act → reflect → repeat.  Each cycle
either advances the plan or revises it based on reflection feedback.  The
loop terminates when the plan is complete, a retry limit is hit, or the
caller cancels.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from ..gateway import AIGateway
from ..memory.service import MemoryService
from .candidates import pick_candidate, resolve_candidates
from .capability_registry import CapabilityRegistry
from .confidence import ConfidenceScorer
from .planning import Goal, Plan, Planner, PlanStatus
from .reasoning import ReasoningEngine, ReasoningResult, ReasoningStrategy
from .reflection import Critique, CritiqueSeverity, ReflectionResult, Reflector
from .state import AgentStateStore, ReflectionHistory
from .telemetry import Telemetry, TelemetryEvent
from .tool_metrics import ToolMetrics
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
    # Heuristic score in [0, 1] from ConfidenceScorer (D4 inputs). None when no run started.
    confidence: float | None = None


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
        telemetry: Telemetry | None = None,
        capabilities: CapabilityRegistry | None = None,
        enable_phase2: bool = False,
        state_dir: Path | None = None,
        enable_metric_selection: bool = False,
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
        # None disables telemetry entirely; events are only written when a sink is given.
        self.telemetry = telemetry
        # Stored for callers that share one registry; not read by the Phase 1 loop.
        self.capabilities = capabilities
        self._scorer = ConfidenceScorer()

        # Phase 2 is off unless enable_phase2 is passed. Passing state_dir alone is an error,
        # so the flag and the directory cannot disagree.
        self._phase2 = enable_phase2
        self._state_dir = Path(state_dir) if state_dir is not None else None
        if enable_phase2 and self._state_dir is None:
            raise ValueError("enable_phase2=True requires state_dir")
        if not enable_phase2 and self._state_dir is not None:
            raise ValueError("state_dir given without enable_phase2=True")
        # Metric selection needs the persisted ToolMetrics, so it requires Phase 2 state.
        if enable_metric_selection and not enable_phase2:
            raise ValueError(
                "enable_metric_selection=True requires enable_phase2=True and state_dir"
            )
        self._metric_selection = enable_metric_selection
        if enable_phase2:
            self.goal_store = AgentStateStore(self._state_dir)
            self.reflection_history = ReflectionHistory(self._state_dir)
            self.tool_metrics = ToolMetrics.load(self._state_dir / "tool_metrics.json")

    async def run(self, goal: Goal) -> OrchestratorResult:
        """Execute the full AGI loop for *goal*."""
        started = time.perf_counter()
        cycles: list[CycleRecord] = []
        self._persist(goal.id, "goal_store",
                      lambda: self.goal_store.record_goal(goal.id, goal.description, "running"))

        self._emit(
            TelemetryEvent.GOAL_RECEIVED,
            goal.id,
            {"success_criteria": len(goal.success_criteria)},
        )
        plan = await self.planner.decompose(goal)
        self._emit(TelemetryEvent.PLAN_GENERATED, goal.id, {"task_count": len(plan.tasks)})
        if not plan.tasks:
            self._emit(
                TelemetryEvent.EXECUTION_COMPLETED,
                goal.id,
                {"success": False, "cycles": 0, "confidence": None},
            )
            self._persist(goal.id, "goal_store",
                          lambda: self.goal_store.record_goal(goal.id, goal.description, "failed"))
            return OrchestratorResult(
                goal=goal,
                plan=plan,
                success=False,
                summary="Planner produced no tasks",
                total_ms=(time.perf_counter() - started) * 1000,
            )

        plan.status = PlanStatus.IN_PROGRESS
        retries: dict[str, int] = {}
        # Selection runs once per task. A retry reuses that decision, so retry cannot
        # become tool failover. See docs/plans/2026-11-09-pb4-retry-selection-stability.
        selections: dict[str, tuple[str | None, str, list[str], str]] = {}

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

            if task.id not in selections:
                selections[task.id] = self._select_tool_name(task)
            selected_name, selection_mode, candidate_names, selection_reason = selections[task.id]
            if selected_name is not None:
                tool_call = ToolCall(
                    tool_name=selected_name,
                    arguments={"task": task.title, "description": task.description},
                )
                selected_payload: dict[str, Any] = {
                    "cycle": cycle_num,
                    "task_id": task.id,
                    "tool_name": selected_name,
                }
                # Only metric-enabled runs add these fields, so the default payload is unchanged.
                if self._metric_selection:
                    selected_payload["selection_mode"] = selection_mode
                    selected_payload["hinted_tool"] = task.tool_hint
                    selected_payload["candidate_tools"] = candidate_names
                    selected_payload["selection_reason"] = selection_reason
                self._emit(TelemetryEvent.TOOL_SELECTED, goal.id, selected_payload)
                call_started = time.perf_counter()
                tool_result = await self.tools.execute(tool_call)
                record.tool_results.append(tool_result)
                elapsed_ms = (time.perf_counter() - call_started) * 1000
                if self._phase2:
                    self._persist(goal.id, "tool_metrics", self.tool_metrics.record,
                                  tool_call.tool_name, success=tool_result.success,
                                  latency_ms=elapsed_ms)

            output_text = reasoning.conclusion
            if record.tool_results:
                last_tool = record.tool_results[-1]
                if last_tool.success and last_tool.output:
                    output_text += f"\n\nTool output: {last_tool.output}"

            failed_tool = self._last_failed_tool(record)
            reflection = await self.reflector.evaluate(
                question=task.title,
                output=output_text,
                criteria=goal.success_criteria,
            )
            if failed_tool is not None and not self._retry_follows(reflection, task, retries):
                # Terminal tool failure with no retry to come. Reflection must see it (bounded
                # text: tool name and error class only), and the result must not read as success.
                failure_line = (
                    f"Tool failed: {failed_tool.tool_name} "
                    f"({failed_tool.error_type or 'UnknownError'})"
                )
                reflection = await self.reflector.evaluate(
                    question=task.title,
                    output=f"{output_text}\n\n{failure_line}",
                    criteria=goal.success_criteria,
                )
                if not self._retry_follows(reflection, task, retries) and not reflection.has_errors:
                    reflection = reflection.model_copy(update={"critiques": [
                        *reflection.critiques,
                        Critique(aspect="execution", severity=CritiqueSeverity.ERROR,
                                 description=failure_line),
                    ]})
            record.reflection = reflection
            if self._phase2:
                self._persist(goal.id, "reflection_history", self.reflection_history.record,
                              goal.id,
                              overall_quality=reflection.overall_quality,
                              should_retry=reflection.should_retry,
                              critique_count=len(reflection.critiques))
            self._emit(
                TelemetryEvent.REFLECTION_COMPLETED,
                goal.id,
                {
                    "cycle": cycle_num,
                    "task_id": task.id,
                    "critiques": len(reflection.critiques),
                    "should_retry": reflection.should_retry,
                    "has_errors": reflection.has_errors,
                },
            )

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
        confidence = self._score_confidence(cycles)

        self._persist(goal.id, "goal_store", lambda: self.goal_store.record_goal(
            goal.id, goal.description, "completed" if success else "failed"))
        self._persist(goal.id, "tool_metrics", lambda: self.tool_metrics.save(
            self._state_dir / "tool_metrics.json"))

        self._emit(
            TelemetryEvent.EXECUTION_COMPLETED,
            goal.id,
            {
                "success": success,
                "cycles": len(cycles),
                "confidence": confidence,
            },
        )

        return OrchestratorResult(
            goal=goal,
            plan=plan,
            cycles=cycles,
            success=success,
            summary=summary,
            total_ms=total_ms,
            confidence=confidence,
        )

    def _select_tool_name(self, task: Any) -> tuple[str | None, str, list[str], str]:
        """Pick the tool to execute for *task*.

        Returns (selected name or None, selection mode, candidate names, reason code).
        Decision logic is unchanged from the metric-selection contract. The extra values
        only describe the decision that was made. They never influence it.

        Reason codes map to real branches:
          hint_default     no tool, or metric selection off
          single_candidate metric selection on, only the hint resolved
          metric_ranked    metric selection on, ranking chose among several candidates
        """
        hint = task.tool_hint
        if not hint or not self.tools.get(hint):
            return None, "default", [], "hint_default"
        if not self._metric_selection:
            return hint, "default", [], "hint_default"
        candidates = resolve_candidates(hint, self.tools)
        candidate_names = [candidate.name for candidate in candidates]
        if len(candidates) <= 1:
            return hint, "default", candidate_names, "single_candidate"
        winner = pick_candidate(candidates, self.tool_metrics)
        return winner.name, "metrics", candidate_names, "metric_ranked"

    @staticmethod
    def _last_failed_tool(record: CycleRecord) -> ToolResult | None:
        """The attempt's tool result if its tool failed, otherwise None."""
        if record.tool_results and not record.tool_results[-1].success:
            return record.tool_results[-1]
        return None

    def _retry_follows(
        self, reflection: ReflectionResult, task: Any, retries: dict[str, int]
    ) -> bool:
        """True when the existing retry branch will send this task back for another attempt."""
        return reflection.should_retry and retries.get(task.id, 0) < self.max_retries_per_task

    def _persist(
        self,
        goal_id: str,
        component: str,
        write: Callable[..., Any],
        *args: Any,
        **kwargs: Any,
    ) -> None:
        """Run one Phase 2 write. A failure is logged as an event and never stops the run."""
        if not self._phase2:
            return
        try:
            write(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 - persistence must not break a run
            log.warning("Phase 2 write to %s failed: %s", component, type(exc).__name__)
            self._emit(
                TelemetryEvent.PERSISTENCE_FAILED,
                goal_id,
                {"component": component, "error_type": type(exc).__name__},
            )

    def _score_confidence(self, cycles: list[CycleRecord]) -> float:
        """Apply the D4 mapping: evidence and contradictions counted across the run.

        Evidence = successful tool results + completed tasks whose reflection had no critiques.
        Contradictions = critiques with severity error or critical. Heuristic, not calibrated.
        """
        evidence = 0
        contradictions = 0
        for record in cycles:
            evidence += sum(1 for result in record.tool_results if result.success)
            reflection = record.reflection
            if reflection is None:
                continue
            contradictions += sum(
                1
                for critique in reflection.critiques
                if critique.severity in (CritiqueSeverity.ERROR, CritiqueSeverity.CRITICAL)
            )
            if record.outcome == "completed" and not reflection.critiques:
                evidence += 1
        return self._scorer.score(evidence, contradictions).score

    def _emit(self, event: TelemetryEvent, goal_id: str, payload: dict[str, Any]) -> None:
        if self.telemetry is not None:
            self.telemetry.record(event, goal_id=goal_id, payload=payload)

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
