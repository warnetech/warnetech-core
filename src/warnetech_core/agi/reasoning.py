"""Structured reasoning engine.

Provides chain-of-thought and tree-of-thought reasoning strategies on top
of the AI gateway.  Each strategy produces a sequence of :class:`ThoughtStep`
objects that record the reasoning trace for inspection and debugging.
"""

from __future__ import annotations

import json
import logging
import time
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from ..gateway import AIGateway

log = logging.getLogger("warnetech.agi.reasoning")


class ReasoningStrategy(str, Enum):
    CHAIN_OF_THOUGHT = "chain_of_thought"
    TREE_OF_THOUGHT = "tree_of_thought"
    STEP_BACK = "step_back"


class ThoughtStep(BaseModel):
    """One step in a reasoning trace."""

    index: int
    content: str
    confidence: float = Field(ge=0.0, le=1.0, default=0.5)
    strategy: ReasoningStrategy = ReasoningStrategy.CHAIN_OF_THOUGHT
    elapsed_ms: float = 0.0
    metadata: dict[str, Any] = Field(default_factory=dict)


class ReasoningResult(BaseModel):
    """Complete output of a reasoning pass."""

    question: str
    conclusion: str
    steps: list[ThoughtStep] = Field(default_factory=list)
    strategy: ReasoningStrategy = ReasoningStrategy.CHAIN_OF_THOUGHT
    total_ms: float = 0.0


_COT_SYSTEM = (
    "You are a precise reasoning engine. Think step by step. "
    "For each step, output a JSON object: "
    '{"step": <n>, "thought": "<text>", "confidence": <0.0-1.0>}. '
    "After all steps, output a final JSON object: "
    '{"conclusion": "<text>", "confidence": <0.0-1.0>}. '
    "Output ONLY valid JSON objects, one per line."
)

_STEP_BACK_SYSTEM = (
    "You are a reasoning engine that uses the step-back technique. "
    "First, identify the higher-level principle or abstraction behind the question. "
    "Then reason from that abstraction to the specific answer. "
    "Output JSON objects, one per line: "
    '{"step": 1, "thought": "<abstraction>", "confidence": <0.0-1.0>} '
    '{"step": 2, "thought": "<reasoning from abstraction>", "confidence": <0.0-1.0>} '
    '... {"conclusion": "<text>", "confidence": <0.0-1.0>}'
)


class ReasoningEngine:
    """Structured multi-step reasoning over the gateway.

    Example::

        engine = ReasoningEngine(gateway)
        result = await engine.reason(
            "Why does Python use the GIL?",
            strategy=ReasoningStrategy.CHAIN_OF_THOUGHT,
        )
        for step in result.steps:
            print(f"Step {step.index}: {step.content}")
        print(f"Conclusion: {result.conclusion}")
    """

    def __init__(
        self,
        gateway: AIGateway,
        *,
        model: str = "chat.default",
        project: str = "agi-reasoning",
        max_steps: int = 10,
    ) -> None:
        self.gateway = gateway
        self.model = model
        self.project = project
        self.max_steps = max_steps

    async def reason(
        self,
        question: str,
        *,
        strategy: ReasoningStrategy = ReasoningStrategy.CHAIN_OF_THOUGHT,
        context: str | None = None,
        max_steps: int | None = None,
    ) -> ReasoningResult:
        """Run a structured reasoning pass over *question*."""
        limit = max_steps or self.max_steps
        started = time.perf_counter()

        if strategy == ReasoningStrategy.STEP_BACK:
            system = _STEP_BACK_SYSTEM
        else:
            system = _COT_SYSTEM

        user_content = question
        if context:
            user_content = f"Context:\n{context}\n\nQuestion:\n{question}"

        response = await self.gateway.complete(
            self.model,
            user_content,
            system=system,
            project=self.project,
            max_tokens=2048,
            temperature=0.3,
        )

        steps, conclusion = self._parse_response(response.text, strategy, limit)
        total_ms = (time.perf_counter() - started) * 1000

        return ReasoningResult(
            question=question,
            conclusion=conclusion,
            steps=steps,
            strategy=strategy,
            total_ms=total_ms,
        )

    def _parse_response(
        self,
        text: str,
        strategy: ReasoningStrategy,
        max_steps: int,
    ) -> tuple[list[ThoughtStep], str]:
        """Parse structured JSON output into thought steps."""
        steps: list[ThoughtStep] = []
        conclusion = ""

        for line in text.strip().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                if not conclusion:
                    conclusion = line
                continue

            if "conclusion" in obj:
                conclusion = obj["conclusion"]
            elif "step" in obj or "thought" in obj:
                if len(steps) >= max_steps:
                    continue
                step_started = time.perf_counter()
                steps.append(
                    ThoughtStep(
                        index=obj.get("step", len(steps) + 1),
                        content=obj.get("thought", ""),
                        confidence=float(obj.get("confidence", 0.5)),
                        strategy=strategy,
                        elapsed_ms=(time.perf_counter() - step_started) * 1000,
                    )
                )

        if not conclusion and steps:
            conclusion = steps[-1].content

        return steps, conclusion
