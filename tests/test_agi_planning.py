"""Tests for warnetech_core.agi.planning."""

from __future__ import annotations

import json

from warnetech_core.agi.planning import (
    Goal,
    Plan,
    Planner,
    PlanStatus,
    TaskNode,
)


class TestTaskNode:
    def test_defaults(self):
        t = TaskNode(title="do thing")
        assert t.status == PlanStatus.PENDING
        assert t.depends_on == []

    def test_mark_complete(self):
        t = TaskNode(title="x")
        t.mark_complete("done")
        assert t.status == PlanStatus.COMPLETED
        assert t.result == "done"

    def test_mark_failed(self):
        t = TaskNode(title="x")
        t.mark_failed("bad")
        assert t.status == PlanStatus.FAILED
        assert t.result == "bad"

    def test_is_ready(self):
        t = TaskNode(title="x")
        assert t.is_ready is True
        t.depends_on = ["other"]
        assert t.is_ready is False


class TestPlan:
    def _make_plan(self):
        t1 = TaskNode(title="first", id="a")
        t2 = TaskNode(title="second", id="b", depends_on=["a"])
        return Plan(goal=Goal(description="test"), tasks=[t1, t2])

    def test_ready_tasks(self):
        plan = self._make_plan()
        ready = plan.ready_tasks()
        assert len(ready) == 1
        assert ready[0].id == "a"

    def test_ready_after_complete(self):
        plan = self._make_plan()
        plan.tasks[0].mark_complete()
        ready = plan.ready_tasks()
        assert len(ready) == 1
        assert ready[0].id == "b"

    def test_is_complete(self):
        plan = self._make_plan()
        assert plan.is_complete() is False
        plan.tasks[0].mark_complete()
        plan.tasks[1].mark_complete()
        assert plan.is_complete() is True

    def test_is_failed(self):
        plan = self._make_plan()
        assert plan.is_failed() is False
        plan.tasks[0].mark_failed("err")
        assert plan.is_failed() is True

    def test_progress(self):
        plan = self._make_plan()
        assert plan.progress() == 0.0
        plan.tasks[0].mark_complete()
        assert plan.progress() == 0.5


class TestPlanner:
    def test_parse_tasks_valid(self):
        planner = Planner.__new__(Planner)
        raw = json.dumps(
            [
                {"title": "A", "description": "do A", "depends_on": []},
                {"title": "B", "description": "do B", "depends_on": ["A"]},
            ]
        )
        tasks = planner._parse_tasks(raw)
        assert len(tasks) == 2
        assert tasks[0].title == "A"
        assert tasks[1].title == "B"
        assert tasks[1].depends_on == [tasks[0].id]

    def test_parse_tasks_bad_json(self):
        planner = Planner.__new__(Planner)
        tasks = planner._parse_tasks("not json")
        assert tasks == []

    def test_parse_tasks_markdown_fenced(self):
        planner = Planner.__new__(Planner)
        raw = '```json\n[{"title": "X", "description": "y"}]\n```'
        tasks = planner._parse_tasks(raw)
        assert len(tasks) == 1
        assert tasks[0].title == "X"
