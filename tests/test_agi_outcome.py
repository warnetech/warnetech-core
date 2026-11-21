"""P2-2 outcome analysis tests (contract approved in docs/plans/2026-11-09-phase-2/plan.md)."""

from __future__ import annotations

import math

import pytest

from warnetech_core.agi.outcome import OutcomeAnalysis, analyze_outcome
from warnetech_core.agi.reflection import Critique, CritiqueSeverity, ReflectionResult


def _reflection(
    *,
    has_errors: bool,
    should_retry: bool,
    overall_quality: float = 0.9,
) -> ReflectionResult:
    critiques = []
    if has_errors:
        critiques.append(
            Critique(
                aspect="correctness",
                severity=CritiqueSeverity.ERROR,
                description="wrong answer",
            )
        )
    return ReflectionResult(
        critiques=critiques,
        overall_quality=overall_quality,
        should_retry=should_retry,
    )


# AC1: successful reflection never retries, whatever should_retry says.


def test_successful_reflection_is_success_without_retry():
    outcome = analyze_outcome(_reflection(has_errors=False, should_retry=False))
    assert outcome == OutcomeAnalysis(success=True, retry=False, confidence=0.9, reason="success")


def test_success_with_should_retry_true_still_has_no_retry():
    # AC7: success=True and retry=True must never be produced together.
    outcome = analyze_outcome(_reflection(has_errors=False, should_retry=True))
    assert outcome.success is True
    assert outcome.retry is False
    assert outcome.reason == "success"


def test_warning_critique_does_not_count_as_failure():
    reflection = ReflectionResult(
        critiques=[
            Critique(
                aspect="style",
                severity=CritiqueSeverity.WARNING,
                description="meh",
            )
        ],
        overall_quality=0.8,
        should_retry=True,
    )
    outcome = analyze_outcome(reflection)
    assert outcome.success is True
    assert outcome.retry is False


# AC2: retryable failure.


def test_failure_with_should_retry_recommends_retry():
    outcome = analyze_outcome(_reflection(has_errors=True, should_retry=True))
    assert outcome == OutcomeAnalysis(
        success=False, retry=True, confidence=0.9, reason="retry_recommended"
    )


# AC3: non-retryable failure.


def test_failure_without_should_retry_does_not_retry():
    outcome = analyze_outcome(_reflection(has_errors=True, should_retry=False))
    assert outcome == OutcomeAnalysis(success=False, retry=False, confidence=0.9, reason="failed")


# AC8: no hard-coded quality threshold. Low quality alone must not trigger retry.


def test_low_quality_without_should_retry_does_not_retry():
    # Regression guard: the rejected 0.70 threshold must not reappear.
    outcome = analyze_outcome(
        _reflection(has_errors=True, should_retry=False, overall_quality=0.20)
    )
    assert outcome.retry is False
    assert outcome.success is False
    assert outcome.confidence == 0.20


# AC4 and AC5: confidence is overall_quality, passed through exactly, with inclusive bounds.


@pytest.mark.parametrize("quality", [0.0, 0.69, 0.70, 0.71, 1.0])
def test_confidence_is_overall_quality_exactly(quality):
    outcome = analyze_outcome(
        _reflection(has_errors=False, should_retry=False, overall_quality=quality)
    )
    assert outcome.confidence == quality


# AC6: invalid confidence is rejected, never clamped. model_construct bypasses the
# pydantic bounds on ReflectionResult, so P2-2 has to defend itself.


@pytest.mark.parametrize("bad", [-0.01, 1.01, float("nan"), float("inf"), float("-inf")])
def test_invalid_confidence_raises_value_error(bad):
    reflection = ReflectionResult.model_construct(
        critiques=[], overall_quality=bad, should_retry=False
    )
    with pytest.raises(ValueError):
        analyze_outcome(reflection)


def test_out_of_range_value_is_not_silently_clamped():
    reflection = ReflectionResult.model_construct(
        critiques=[], overall_quality=1.4, should_retry=False
    )
    with pytest.raises(ValueError):
        analyze_outcome(reflection)


# AC10: deterministic.


def test_same_reflection_gives_same_outcome():
    reflection = _reflection(has_errors=True, should_retry=True, overall_quality=0.42)
    assert analyze_outcome(reflection) == analyze_outcome(reflection)
    assert not math.isnan(analyze_outcome(reflection).confidence)


# AC7: the invariant holds across the full decision matrix.


@pytest.mark.parametrize(
    ("has_errors", "should_retry", "success", "retry", "reason"),
    [
        (False, False, True, False, "success"),
        (False, True, True, False, "success"),
        (True, False, False, False, "failed"),
        (True, True, False, True, "retry_recommended"),
    ],
)
def test_decision_matrix(has_errors, should_retry, success, retry, reason):
    outcome = analyze_outcome(_reflection(has_errors=has_errors, should_retry=should_retry))
    assert (outcome.success, outcome.retry, outcome.reason) == (success, retry, reason)
    assert not (outcome.success and outcome.retry)
