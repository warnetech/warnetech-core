"""Tests for the supporting AGI modules: context budgeting, agent pipeline, temporal state."""

from __future__ import annotations

import pytest

from warnetech_core.agi import AgentOrchestrator, ContextWindow, TemporalEngine

# ------------------------------------------------------------ ContextWindow


class TestContextWindowEstimate:
    def test_empty_text_is_zero_tokens(self):
        assert ContextWindow().estimate_tokens("") == 0

    def test_roughly_four_characters_per_token(self):
        assert ContextWindow().estimate_tokens("a" * 400) == 100

    def test_short_text_is_at_least_one_token(self):
        assert ContextWindow().estimate_tokens("hi") == 1

    def test_effective_limit_subtracts_safety_margin(self):
        assert ContextWindow(max_tokens=1000, safety_margin=200).effective_limit == 800


class TestContextWindowAssemble:
    def test_includes_system_query_and_assistant_cue(self):
        prompt = ContextWindow().assemble_prompt("Be terse.", [], [], "What is 2+2?")
        assert prompt.startswith("System: Be terse.")
        assert "User: What is 2+2?" in prompt
        assert prompt.endswith("Assistant:")

    def test_empty_system_prompt_is_omitted(self):
        prompt = ContextWindow().assemble_prompt("", [], [], "hello")
        assert "System:" not in prompt

    def test_rag_is_capped_at_half_the_remaining_budget(self):
        window = ContextWindow(max_tokens=1000, safety_margin=0)
        chunks = [{"content": "A" * 1000}, {"content": "B" * 1000}]
        prompt = window.assemble_prompt("", chunks, [], "q")
        # ~999 tokens remain; RAG may use ~499. One ~253-token chunk fits, two do not.
        assert "AAAA" in prompt
        assert "BBBB" not in prompt

    def test_history_keeps_the_most_recent_messages(self):
        window = ContextWindow(max_tokens=200, safety_margin=0)
        history = [
            {"role": "user", "content": "FIRST" + "x" * 400},
            {"role": "assistant", "content": "SECOND" + "x" * 400},
            {"role": "user", "content": "LAST" + "x" * 400},
        ]
        prompt = window.assemble_prompt("", [], history, "q")
        assert "LAST" in prompt
        assert "FIRST" not in prompt

    def test_history_order_is_preserved(self):
        window = ContextWindow(max_tokens=4000, safety_margin=0)
        history = [
            {"role": "user", "content": "one"},
            {"role": "assistant", "content": "two"},
        ]
        prompt = window.assemble_prompt("", [], history, "three")
        assert (
            prompt.index("User: one") < prompt.index("Assistant: two") < prompt.index("User: three")
        )

    def test_query_is_kept_even_when_system_prompt_overflows(self):
        window = ContextWindow(max_tokens=10, safety_margin=0)
        prompt = window.assemble_prompt(
            "S" * 500, [{"content": "rag"}], [{"content": "hist"}], "ask"
        )
        assert "User: ask" in prompt
        assert "[RAG Context]" not in prompt
        assert "hist" not in prompt

    def test_missing_content_is_treated_as_empty(self):
        window = ContextWindow()
        prompt = window.assemble_prompt(
            "", [{"content": None}], [{"role": "user", "content": None}], "q"
        )
        assert "None" not in prompt


# ---------------------------------------------------------- AgentOrchestrator


def _append(name: str):
    async def handler(state: dict) -> dict:
        state.setdefault("trail", []).append(name)
        return state

    return handler


class TestAgentOrchestrator:
    async def test_runs_agents_in_registration_order_and_threads_state(self):
        orch = AgentOrchestrator()
        orch.register_agent("a", _append("a"))
        orch.register_agent("b", _append("b"))

        state = await orch.execute({"task": "t"})

        assert state["trail"] == ["a", "b"]
        assert state["task"] == "t"
        assert state["_last_executed_agent"] == "b"

    async def test_re_registering_does_not_duplicate_pipeline_entry(self):
        orch = AgentOrchestrator()
        orch.register_agent("a", _append("a"))
        orch.register_agent("a", _append("a"))
        assert orch.execution_pipeline == ["a"]

    async def test_set_pipeline_controls_order(self):
        orch = AgentOrchestrator()
        orch.register_agent("a", _append("a"))
        orch.register_agent("b", _append("b"))
        orch.set_pipeline(["b", "a"])

        state = await orch.execute({})

        assert state["trail"] == ["b", "a"]

    async def test_set_pipeline_rejects_unregistered_names(self):
        orch = AgentOrchestrator()
        orch.register_agent("a", _append("a"))
        with pytest.raises(ValueError, match="ghost"):
            orch.set_pipeline(["a", "ghost"])

    async def test_missing_handler_is_skipped(self):
        orch = AgentOrchestrator()
        orch.register_agent("a", _append("a"))
        orch.execution_pipeline = ["ghost", "a"]

        state = await orch.execute({})

        assert state["trail"] == ["a"]

    async def test_exception_stops_the_run_and_records_it(self):
        async def boom(state: dict) -> dict:
            raise RuntimeError("kaput")

        orch = AgentOrchestrator()
        orch.register_agent("a", _append("a"))
        orch.register_agent("boom", boom)
        orch.register_agent("c", _append("c"))

        state = await orch.execute({})

        assert state["failed_agent"] == "boom"
        assert "kaput" in state["error"]
        assert state["trail"] == ["a"]  # "c" never ran

    async def test_non_dict_return_is_reported_as_failure(self):
        async def returns_none(state: dict):
            return None

        orch = AgentOrchestrator()
        orch.register_agent("bad", returns_none)  # type: ignore[arg-type]
        orch.register_agent("after", _append("after"))

        state = await orch.execute({})

        assert state["failed_agent"] == "bad"
        assert "NoneType" in state["error"]
        assert "trail" not in state

    async def test_caller_payload_is_not_mutated(self):
        orch = AgentOrchestrator()
        orch.register_agent("a", _append("a"))
        payload: dict = {"task": "t"}

        await orch.execute(payload)

        assert payload == {"task": "t"}


# ----------------------------------------------------------- TemporalEngine


class TestTemporalEngine:
    async def test_hooks_run_in_order_and_state_persists_across_steps(self):
        engine = TemporalEngine({"count": 0})

        def bump(state: dict) -> dict:
            return {**state, "count": state["count"] + 1}

        engine.register_hook(bump)
        engine.register_hook(lambda s: (s.setdefault("seen", []).append(s["count"]), None)[1])

        await engine.step({"note": "first"})
        state = await engine.step({"note": "second"})

        assert state["count"] == 2
        assert state["seen"] == [1, 2]
        assert state["note"] == "second"

    async def test_async_hook_is_awaited(self):
        engine = TemporalEngine()

        async def later(state: dict) -> dict:
            state["done"] = True
            return state

        engine.register_hook(later)
        state = await engine.step({})

        assert state["done"] is True

    async def test_hook_returning_none_keeps_in_place_changes(self):
        engine = TemporalEngine()

        def mutate(state: dict) -> None:
            state["mutated"] = True

        engine.register_hook(mutate)
        state = await engine.step({})

        assert state["mutated"] is True

    async def test_hook_returning_non_dict_raises_clear_error(self):
        engine = TemporalEngine()
        engine.register_hook(lambda s: 42)  # type: ignore[arg-type,return-value]

        with pytest.raises(TypeError, match="must return a dict or None"):
            await engine.step({})

    async def test_initial_state_is_copied_not_aliased(self):
        original = {"k": 1}
        engine = TemporalEngine(original)
        await engine.step({"k": 2})

        assert original == {"k": 1}
        assert engine.state["k"] == 2
