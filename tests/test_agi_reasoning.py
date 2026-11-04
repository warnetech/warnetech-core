"""Tests for warnetech_core.agi.reasoning."""

from __future__ import annotations

import pytest

from warnetech_core.agi.reasoning import (
    ReasoningEngine,
    ReasoningResult,
    ReasoningStrategy,
    ThoughtStep,
)


class TestThoughtStep:
    def test_defaults(self):
        step = ThoughtStep(index=1, content="test")
        assert step.confidence == 0.5
        assert step.strategy == ReasoningStrategy.CHAIN_OF_THOUGHT

    def test_confidence_bounds(self):
        step = ThoughtStep(index=1, content="hi", confidence=0.0)
        assert step.confidence == 0.0
        step = ThoughtStep(index=1, content="hi", confidence=1.0)
        assert step.confidence == 1.0

    def test_invalid_confidence(self):
        with pytest.raises(ValueError):
            ThoughtStep(index=1, content="hi", confidence=1.5)


class TestReasoningResult:
    def test_empty(self):
        r = ReasoningResult(question="q", conclusion="c")
        assert r.steps == []
        assert r.strategy == ReasoningStrategy.CHAIN_OF_THOUGHT


class TestReasoningEngine:
    def test_parse_response_json_lines(self):
        engine = ReasoningEngine.__new__(ReasoningEngine)
        engine.max_steps = 10
        text = (
            '{"step": 1, "thought": "first", "confidence": 0.8}\n'
            '{"step": 2, "thought": "second", "confidence": 0.9}\n'
            '{"conclusion": "done", "confidence": 0.95}\n'
        )
        steps, conclusion = engine._parse_response(
            text,
            ReasoningStrategy.CHAIN_OF_THOUGHT,
            10,
        )
        assert len(steps) == 2
        assert steps[0].content == "first"
        assert steps[1].content == "second"
        assert conclusion == "done"

    def test_parse_response_fallback(self):
        engine = ReasoningEngine.__new__(ReasoningEngine)
        engine.max_steps = 10
        text = "just a plain answer"
        steps, conclusion = engine._parse_response(
            text,
            ReasoningStrategy.CHAIN_OF_THOUGHT,
            10,
        )
        assert steps == []
        assert conclusion == "just a plain answer"

    def test_parse_response_max_steps(self):
        engine = ReasoningEngine.__new__(ReasoningEngine)
        engine.max_steps = 2
        text = "\n".join(
            f'{{"step": {i}, "thought": "s{i}", "confidence": 0.5}}' for i in range(1, 6)
        )
        steps, _ = engine._parse_response(
            text,
            ReasoningStrategy.CHAIN_OF_THOUGHT,
            2,
        )
        assert len(steps) == 2
