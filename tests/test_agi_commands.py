"""Tests for the ``warnetech agi`` command group. Offline: no network, no API keys."""

from __future__ import annotations

import json
from types import SimpleNamespace

from warnetech_core.agi_commands import handle_run
from warnetech_core.cli import main as cli_main

PLAN = json.dumps([{"title": "draft", "description": "write the draft"}])
REASON = '{"step": 1, "thought": "work it out", "confidence": 0.9}\n{"conclusion": "draft text"}'
REFLECT_PASS = '{"critiques": [], "overall_quality": 0.9, "should_retry": false}'


class FakeGateway:
    """Answers the planner, reasoner and reflector by their system prompts."""

    def __init__(self, plan: str = PLAN) -> None:
        self.plan = plan
        self.closed = False

    async def complete(self, model, messages, *, system=None, **_kw):
        system = system or ""
        if "task planner" in system:
            text = self.plan
        elif "reasoning engine" in system:
            text = REASON
        else:
            text = REFLECT_PASS
        return SimpleNamespace(text=text)

    async def aclose(self) -> None:
        self.closed = True


def test_run_completes_and_exits_zero(capsys):
    gateway = FakeGateway()

    rc = handle_run({"goal": "write a memo", "_gateway": gateway})

    out = capsys.readouterr().out
    assert rc == 0
    assert "[completed] draft" in out
    assert "Completed: 1/1 tasks done" in out
    assert gateway.closed is True


def test_run_exits_one_when_the_planner_returns_nothing(capsys):
    rc = handle_run({"goal": "vague", "_gateway": FakeGateway(plan="[]")})

    assert rc == 1
    assert "Planner produced no tasks" in capsys.readouterr().out


def test_offline_mode_runs_end_to_end_without_keys(monkeypatch, capsys):
    monkeypatch.setenv("WARNETECH_OFFLINE", "1")

    rc = cli_main(["agi", "run", "summarise the readme"])

    # The echo model does not return a JSON plan, so the run stops cleanly with exit 1.
    assert rc == 1
    assert "Planner produced no tasks" in capsys.readouterr().out
