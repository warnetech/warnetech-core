"""Tests for warnetech_core.agi.reflection."""

from __future__ import annotations

import json

from warnetech_core.agi.reflection import (
    Critique,
    CritiqueSeverity,
    ReflectionResult,
    Reflector,
)


class TestCritique:
    def test_create(self):
        c = Critique(
            aspect="correctness",
            severity=CritiqueSeverity.ERROR,
            description="wrong answer",
        )
        assert c.severity == CritiqueSeverity.ERROR


class TestReflectionResult:
    def test_has_errors_true(self):
        r = ReflectionResult(
            critiques=[
                Critique(
                    aspect="x",
                    severity=CritiqueSeverity.ERROR,
                    description="bad",
                )
            ]
        )
        assert r.has_errors is True

    def test_has_errors_false(self):
        r = ReflectionResult(
            critiques=[
                Critique(
                    aspect="x",
                    severity=CritiqueSeverity.INFO,
                    description="ok",
                )
            ]
        )
        assert r.has_errors is False


class TestReflector:
    def test_parse_valid(self):
        reflector = Reflector.__new__(Reflector)
        text = json.dumps(
            {
                "critiques": [
                    {
                        "aspect": "accuracy",
                        "severity": "warning",
                        "description": "slightly off",
                        "suggestion": "fix it",
                        "confidence": 0.7,
                    }
                ],
                "overall_quality": 0.8,
                "should_retry": False,
                "revised_output": None,
            }
        )
        result = reflector._parse_reflection(text)
        assert len(result.critiques) == 1
        assert result.overall_quality == 0.8
        assert result.should_retry is False

    def test_parse_bad_json(self):
        reflector = Reflector.__new__(Reflector)
        result = reflector._parse_reflection("not json")
        assert len(result.critiques) == 1
        assert result.critiques[0].aspect == "parse_failure"

    def test_parse_markdown_fenced(self):
        reflector = Reflector.__new__(Reflector)
        text = '```json\n{"critiques": [], "overall_quality": 0.9}\n```'
        result = reflector._parse_reflection(text)
        assert result.overall_quality == 0.9
