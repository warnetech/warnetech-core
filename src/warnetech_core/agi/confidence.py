"""Heuristic confidence scoring for AGI outcomes (Phase 1, T4).

Pure function of counted evidence and contradictions. No model calls, no
persistence, no telemetry. The score is a heuristic, not a calibrated probability.
"""

from __future__ import annotations

from pydantic import BaseModel

_BASE_SCORE = 0.50
_PER_EVIDENCE = 0.05
_MAX_EVIDENCE_BONUS = 0.35
_PER_CONTRADICTION = 0.10


class ConfidenceResult(BaseModel):
    """A confidence score in [0.0, 1.0] plus the counts that produced it."""

    score: float
    evidence_count: int
    contradictions: int


class ConfidenceScorer:
    """Scores an outcome from its evidence and contradiction counts.

    Starts at 0.50, adds 0.05 per evidence item up to +0.35, subtracts 0.10 per
    contradiction, then clamps to [0.0, 1.0] and rounds to two decimals.
    """

    def score(self, evidence_count: int, contradictions: int) -> ConfidenceResult:
        if evidence_count < 0:
            raise ValueError(f"evidence_count must be >= 0, got {evidence_count}")
        if contradictions < 0:
            raise ValueError(f"contradictions must be >= 0, got {contradictions}")

        evidence_bonus = min(evidence_count * _PER_EVIDENCE, _MAX_EVIDENCE_BONUS)
        raw = _BASE_SCORE + evidence_bonus - contradictions * _PER_CONTRADICTION
        clamped = max(0.0, min(raw, 1.0))

        return ConfidenceResult(
            score=round(clamped, 2),
            evidence_count=evidence_count,
            contradictions=contradictions,
        )
