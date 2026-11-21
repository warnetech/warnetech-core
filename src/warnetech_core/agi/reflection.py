"""Self-evaluation, trajectory scoring, and episodic consolidation.

The :class:`Reflector` examines outputs (from reasoning, planning, or tool use)
and produces :class:`Critique` objects that identify issues and suggest fixes.

The :class:`TrajectoryScorer` evaluates completed/failed trajectories, extracts
reusable heuristics and post-mortems, and persists them as episodic memories
in warnetech_memory.py that are automatically injected into future planning.py
prompts for similar tasks.
"""

from __future__ import annotations

import json
import logging
import time
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from ..gateway import AIGateway
from ._coerce import strip_code_fence, unit_interval

log = logging.getLogger("warnetech.agi.reflection")


class CritiqueSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class Critique(BaseModel):
    """One issue found during reflection."""

    aspect: str
    severity: CritiqueSeverity
    description: str
    suggestion: str = ""
    confidence: float = Field(ge=0.0, le=1.0, default=0.5)


class ReflectionResult(BaseModel):
    """Output of a reflection pass."""

    critiques: list[Critique] = Field(default_factory=list)
    overall_quality: float = Field(ge=0.0, le=1.0, default=0.5)
    should_retry: bool = False
    revised_output: str | None = None
    elapsed_ms: float = 0.0

    @property
    def has_errors(self) -> bool:
        return any(
            c.severity in (CritiqueSeverity.ERROR, CritiqueSeverity.CRITICAL)
            for c in self.critiques
        )


class TrajectoryScore(BaseModel):
    """Score for a completed or failed trajectory."""

    task_type: str  # e.g. "code_review", "debugging", "planning"
    success: bool
    heuristic: str  # Extracted reusable heuristic or post-mortem
    confidence: float = Field(ge=0.0, le=1.0, default=0.7)
    tags: list[str] = Field(default_factory=list)


_REFLECT_SYSTEM = (
    "You are a critical evaluator. Given an output and the original question, "
    "assess the output for correctness, completeness, and quality. "
    "Output a JSON object with: "
    '"critiques" (array of {"aspect": str, "severity": "info"|"warning"|"error"|"critical", '
    '"description": str, "suggestion": str, "confidence": 0.0-1.0}), '
    '"overall_quality" (0.0-1.0), '
    '"should_retry" (bool — true if quality is too low), '
    '"revised_output" (str or null — a corrected version if needed). '
    "Output ONLY valid JSON."
)


class Reflector:
    """Evaluates and critiques outputs for quality and correctness.

    Example::

        reflector = Reflector(gateway)
        result = await reflector.evaluate(
            question="What causes rust?",
            output="Rust is caused by iron reacting with oxygen and water.",
        )
        if result.should_retry:
            print("Quality too low, retrying")
        for critique in result.critiques:
            print(f"[{critique.severity}] {critique.aspect}: {critique.description}")
    """

    def __init__(
        self,
        gateway: AIGateway,
        *,
        model: str = "chat.default",
        project: str = "agi-reflection",
        quality_threshold: float = 0.6,
    ) -> None:
        self.gateway = gateway
        self.model = model
        self.project = project
        self.quality_threshold = quality_threshold

    async def evaluate(
        self,
        question: str,
        output: str,
        *,
        criteria: list[str] | None = None,
    ) -> ReflectionResult:
        """Evaluate *output* as an answer to *question*."""
        started = time.perf_counter()

        user_content = f"Question:\n{question}\n\nOutput to evaluate:\n{output}"
        if criteria:
            user_content += "\n\nEvaluation criteria:\n" + "\n".join(f"- {c}" for c in criteria)

        response = await self.gateway.complete(
            self.model,
            user_content,
            system=_REFLECT_SYSTEM,
            project=self.project,
            max_tokens=1024,
            temperature=0.2,
        )

        result = self._parse_reflection(response.text)
        result.elapsed_ms = (time.perf_counter() - started) * 1000

        if result.overall_quality < self.quality_threshold and not result.should_retry:
            result.should_retry = True

        return result

    def _parse_reflection(self, text: str) -> ReflectionResult:
        """Parse LLM JSON into a ReflectionResult."""
        text = strip_code_fence(text)

        try:
            obj = json.loads(text)
        except json.JSONDecodeError:
            obj = None

        if not isinstance(obj, dict):
            log.warning("failed to parse reflection output: %s", text[:200])
            return ReflectionResult(
                critiques=[
                    Critique(
                        aspect="parse_failure",
                        severity=CritiqueSeverity.WARNING,
                        description="Reflector output was not a valid JSON object",
                    )
                ],
                overall_quality=0.5,
            )

        raw_critiques = obj.get("critiques")
        critiques: list[Critique] = []
        for raw in raw_critiques if isinstance(raw_critiques, list) else []:
            if not isinstance(raw, dict):
                continue
            try:
                critiques.append(Critique(**raw))
            except (TypeError, ValueError):
                continue

        revised = obj.get("revised_output")
        return ReflectionResult(
            critiques=critiques,
            overall_quality=unit_interval(obj.get("overall_quality")),
            # Only a real JSON true asks for a retry: "false" as a string must not.
            should_retry=obj.get("should_retry") is True,
            revised_output=revised if isinstance(revised, str) else None,
        )


class TrajectoryScorer:
    """Scores completed/failed trajectories and persists heuristics as episodic memories."""

    def __init__(
        self,
        gateway: AIGateway,
        *,
        model: str = "chat.default",
        project: str = "agi-trajectory",
        episodic_store: dict[str, Any] | None = None,
    ) -> None:
        self.gateway = gateway
        self.model = model
        self.project = project
        self.episodic_store = episodic_store or {}  # In-memory episodic memory

    async def score_trajectory(
        self,
        task_type: str,
        trajectory: list[dict[str, Any]],
        success: bool,
        outcome: str = "",
    ) -> TrajectoryScore:
        """Score a completed trajectory and extract heuristics for future use.

        Args:
            task_type: Category of task (e.g., 'code_review', 'debugging')
            trajectory: List of steps taken (e.g., [{"step": "...", "result": "..."}])
            success: Whether trajectory succeeded
            outcome: Final outcome or error message
        """
        trajectory_text = json.dumps(trajectory, indent=2)

        prompt = (
            f"Task type: {task_type}\n"
            f"Success: {success}\n"
            f"Outcome: {outcome}\n\n"
            f"Trajectory:\n{trajectory_text}\n\n"
            "Extract one reusable heuristic or post-mortem from this trajectory. "
            "Be specific and actionable. Output: single line heuristic only."
        )

        response = await self.gateway.complete(
            self.model,
            prompt,
            system=(
                "You extract actionable heuristics from task trajectories. "
                "Output only the heuristic, no explanation."
            ),
            project=self.project,
            max_tokens=256,
            temperature=0.1,
        )

        heuristic = response.text.strip()
        score = TrajectoryScore(
            task_type=task_type,
            success=success,
            heuristic=heuristic,
            tags=["high_priority"],
        )

        # Persist as episodic memory for future planning
        memory_key = f"heuristic:{task_type}:{hash(heuristic) % 10000}"
        self.episodic_store[memory_key] = {
            "trajectory_score": score.dict(),
            "timestamp": time.time(),
        }

        return score
