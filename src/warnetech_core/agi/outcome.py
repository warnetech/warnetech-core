"""Outcome analysis (P2-2): derive an outcome from a ReflectionResult.

Analysis only. Nothing here runs a retry, changes tool selection or writes state.
The mapping is fixed by the approved P2-2 contract in
docs/plans/2026-11-09-phase-2/plan.md.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .reflection import ReflectionResult


@dataclass(frozen=True, slots=True)
class OutcomeAnalysis:
    """Outcome derived from one reflection.

    ``retry`` is a recommendation only. ``confidence`` is the reflection's
    ``overall_quality`` copied through, not a second confidence calculation.
    """

    success: bool
    retry: bool
    confidence: float
    reason: str


def _validated_confidence(value: float) -> float:
    """Return *value* if it is a finite number in [0, 1]. Otherwise raise ValueError.

    Deliberately does not clamp: an out-of-range value means the reflection is corrupt,
    and clamping would hide that.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"confidence must be a number, got {type(value).__name__}")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("confidence must be finite")
    if not 0.0 <= number <= 1.0:
        raise ValueError(f"confidence must be within [0, 1], got {number}")
    return number


def analyze_outcome(reflection: ReflectionResult) -> OutcomeAnalysis:
    """Map a reflection to an outcome.

    success = not has_errors
    retry   = should_retry AND NOT success
    """
    confidence = _validated_confidence(reflection.overall_quality)
    success = not reflection.has_errors
    retry = reflection.should_retry and not success

    if success:
        reason = "success"
    elif retry:
        reason = "retry_recommended"
    else:
        reason = "failed"

    return OutcomeAnalysis(success=success, retry=retry, confidence=confidence, reason=reason)
