"""Tests for warnetech_core.agi.confidence (T4)."""

from __future__ import annotations

import pytest

from warnetech_core.agi.confidence import ConfidenceResult, ConfidenceScorer


def test_no_evidence_no_contradictions_is_neutral_half():
    result = ConfidenceScorer().score(evidence_count=0, contradictions=0)
    assert result.score == 0.50


def test_five_pieces_of_evidence_give_0_75():
    assert ConfidenceScorer().score(evidence_count=5, contradictions=0).score == 0.75


def test_evidence_bonus_is_capped_at_0_85():
    assert ConfidenceScorer().score(evidence_count=10, contradictions=0).score == 0.85
    assert ConfidenceScorer().score(evidence_count=50, contradictions=0).score == 0.85


def test_one_contradiction_reduces_five_evidence_to_0_65():
    assert ConfidenceScorer().score(evidence_count=5, contradictions=1).score == 0.65


def test_three_contradictions_without_evidence_give_0_20():
    assert ConfidenceScorer().score(evidence_count=0, contradictions=3).score == 0.20


def test_score_clamps_to_zero_when_contradictions_dominate():
    assert ConfidenceScorer().score(evidence_count=0, contradictions=6).score == 0.00


def test_result_echoes_inputs():
    result = ConfidenceScorer().score(evidence_count=4, contradictions=2)
    assert isinstance(result, ConfidenceResult)
    assert result.evidence_count == 4
    assert result.contradictions == 2


def test_more_evidence_never_lowers_confidence():
    scorer = ConfidenceScorer()
    scores = [scorer.score(evidence_count=n, contradictions=0).score for n in range(0, 15)]
    assert scores == sorted(scores)
    assert scores[0] < scores[5]


def test_more_contradictions_lower_confidence_until_floor():
    scorer = ConfidenceScorer()
    scores = [scorer.score(evidence_count=5, contradictions=c).score for c in range(0, 8)]
    assert scores == sorted(scores, reverse=True)
    assert scores[0] > scores[1] > scores[2] > scores[3]
    assert scores[-1] == 0.05  # 0.75 - 7 * 0.10, not yet clamped


def test_scores_stay_within_unit_interval_over_a_grid():
    scorer = ConfidenceScorer()
    for evidence in range(0, 30):
        for contradictions in range(0, 30):
            score = scorer.score(evidence_count=evidence, contradictions=contradictions).score
            assert 0.0 <= score <= 1.0


def test_scoring_is_deterministic():
    scorer = ConfidenceScorer()
    first = scorer.score(evidence_count=3, contradictions=1)
    second = scorer.score(evidence_count=3, contradictions=1)
    assert first == second


def test_negative_evidence_is_rejected():
    with pytest.raises(ValueError, match="evidence_count must be >= 0"):
        ConfidenceScorer().score(evidence_count=-1, contradictions=0)


def test_negative_contradictions_are_rejected():
    with pytest.raises(ValueError, match="contradictions must be >= 0"):
        ConfidenceScorer().score(evidence_count=0, contradictions=-1)
