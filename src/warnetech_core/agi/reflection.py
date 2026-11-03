"""Self-evaluation and output correction.

The :class:`Reflector` examines outputs (from reasoning, planning, or tool use)
and produces :class:`Critique` objects that identify issues and suggest fixes.
This is the "inner loop" quality gate — the orchestrator uses it to decide
whether to accept, retry, or revise.
"""

from __future__ import annotations

import json
import logging
import time
from enum import Enum

from pydantic import BaseModel, Field

from ..gateway import AIGateway

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
        text = text.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

        try:
            obj = json.loads(text)
        except json.JSONDecodeError:
            log.warning("failed to parse reflection output: %s", text[:200])
            return ReflectionResult(
                critiques=[
                    Critique(
                        aspect="parse_failure",
                        severity=CritiqueSeverity.WARNING,
                        description="Reflector output was not valid JSON",
                    )
                ],
                overall_quality=0.5,
            )

        critiques = []
        for raw in obj.get("critiques", []):
            try:
                critiques.append(Critique(**raw))
            except Exception:
                continue

        return ReflectionResult(
            critiques=critiques,
            overall_quality=float(obj.get("overall_quality", 0.5)),
            should_retry=bool(obj.get("should_retry", False)),
            revised_output=obj.get("revised_output"),
        )
