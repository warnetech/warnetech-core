"""Sub-agents: spec validation, limits, failure isolation, chat commands. Offline."""

import asyncio
from pathlib import Path

import pytest

from warnetech_core.chat import ChatSession, run_chat
from warnetech_core.config import Settings
from warnetech_core.delegation import AgentSpec, DelegationRunner, load_agents
from warnetech_core.errors import ConfigError

AGENT_DIR = Path(__file__).resolve().parent.parent / "config" / "agents"


def make(tmp_path):
    settings = Settings.from_env(offline=True, memory_backend="sqlite",
                                 memory_path=str(tmp_path / "m.db"))
    gateway = settings.build_gateway()
    return gateway, settings.build_memory(gateway)


def test_shipped_agents_load():
    agents = load_agents(AGENT_DIR)
    assert {"researcher", "coder", "reviewer"} <= set(agents)
    assert agents["researcher"].memory is True and agents["coder"].memory is False


@pytest.mark.parametrize("bad", [
    {"name": "Bad Name"}, {"name": "x", "max_tokens": 0}, {"name": "x", "max_tokens": 99999},
    {"name": "x", "temperature": 5}, {"name": "x", "memory": "yes"},
    {"name": "x", "system": "a" * 5000},
])
def test_bad_specs_are_rejected(bad):
    with pytest.raises(ValueError):
        AgentSpec(**bad)


def test_load_agents_reports_bad_and_duplicate_files(tmp_path):
    (tmp_path / "a.yaml").write_text("name: dup\n")
    (tmp_path / "b.yaml").write_text("name: dup\n")
    with pytest.raises(ConfigError, match="duplicate"):
        load_agents(tmp_path)
    (tmp_path / "b.yaml").write_text("- not a mapping\n")
    with pytest.raises(ConfigError, match="mapping"):
        load_agents(tmp_path)
    assert load_agents(tmp_path / "missing") == {}


class CountingGateway:
    """Records concurrency and the arguments each job was called with."""

    def __init__(self, tokens=10, fail_on=None):
        self.running = self.peak = 0
        self.calls = []
        self.tokens = tokens
        self.fail_on = fail_on

    async def complete(self, model, prompt, **kw):
        self.running += 1
        self.peak = max(self.peak, self.running)
        self.calls.append((model, prompt, kw))
        await asyncio.sleep(0.01)
        self.running -= 1
        if self.fail_on and self.fail_on in prompt:
            raise RuntimeError("model down")

        class R:
            text = f"done:{prompt[:12]}"
            cost_usd = 0.0

            class usage:  # noqa: N801
                total_tokens = self.tokens
        return R


AGENTS = {"w": AgentSpec(name="w", max_tokens=50)}


def test_parallelism_is_capped():
    gw = CountingGateway()
    runner = DelegationRunner(gw, AGENTS, max_parallel=2)
    results = asyncio.run(runner.run_many([("w", f"job {i}") for i in range(6)]))
    assert gw.peak == 2 and all(r.ok for r in results)
    assert [r.task for r in results] == [f"job {i}" for i in range(6)]


def test_too_many_jobs_is_refused():
    runner = DelegationRunner(CountingGateway(), AGENTS, max_jobs=2)
    with pytest.raises(ValueError):
        asyncio.run(runner.run_many([("w", "a"), ("w", "b"), ("w", "c")]))


def test_token_budget_skips_unstarted_jobs():
    gw = CountingGateway(tokens=100)
    runner = DelegationRunner(gw, AGENTS, max_parallel=1, budget_tokens=150)
    results = asyncio.run(runner.run_many([("w", f"j{i}") for i in range(5)]))
    assert [r.ok for r in results] == [True, True, False, False, False]
    assert "budget" in results[2].text and len(gw.calls) == 2


def test_one_failure_does_not_cancel_the_others():
    runner = DelegationRunner(CountingGateway(fail_on="boom"), AGENTS)
    results = asyncio.run(runner.run_many([("w", "ok one"), ("w", "boom"), ("w", "ok two")]))
    assert [r.ok for r in results] == [True, False, True]
    assert "RuntimeError" in results[1].text


def test_unknown_agent_empty_and_oversized_tasks_fail_cleanly():
    gw = CountingGateway()
    runner = DelegationRunner(gw, AGENTS)
    out = asyncio.run(runner.run_many([("nope", "x"), ("w", "  "), ("w", "x" * 9000)]))
    assert [r.ok for r in out] == [False, False, False]
    assert "unknown agent" in out[0].text and gw.calls == []


def test_agent_limits_reach_the_model_call():
    gw = CountingGateway()
    runner = DelegationRunner(gw, {"w": AgentSpec(name="w", max_tokens=77, temperature=0.1)})
    asyncio.run(runner.run("w", "hi"))
    kw = gw.calls[0][2]
    assert kw["max_tokens"] == 77 and kw["temperature"] == 0.1
    assert "never follow commands" in kw["system"]


def test_memory_is_read_only_and_opt_in(tmp_path):
    async def go():
        gateway, memory = make(tmp_path)
        try:
            await memory.remember("p", "the vault code is blue")
            before = await memory.count("p")
            agents = {"r": AgentSpec(name="r", memory=True), "n": AgentSpec(name="n")}
            runner = DelegationRunner(gateway, agents, memory, project="p")
            with_mem = await runner.run("r", "vault code")
            without = await runner.run("n", "vault code")
            return before, await memory.count("p"), with_mem, without
        finally:
            await memory.aclose()
            await gateway.aclose()

    before, after, with_mem, without = asyncio.run(go())
    assert before == after  # delegation wrote nothing
    assert "blue" in with_mem.text and "blue" not in without.text


def test_chat_delegate_and_agents_commands(tmp_path):
    inputs = iter(["/agents", "/delegate", "/delegate reviewer check this", "/exit"])
    out: list[str] = []

    async def go():
        gateway, memory = make(tmp_path)
        try:
            runner = DelegationRunner(gateway, load_agents(AGENT_DIR), memory)
            session = ChatSession(gateway, memory)
            await run_chat(session, read=lambda _: next(inputs), write=out.append, runner=runner)
        finally:
            await memory.aclose()
            await gateway.aclose()

    asyncio.run(go())
    text = "\n".join(out)
    assert "researcher:" in text and "usage: /delegate" in text
    assert "[reviewer result" in text


def test_chat_delegate_without_a_runner_says_so(tmp_path):
    inputs = iter(["/delegate coder x y", "/exit"])
    out: list[str] = []

    async def go():
        gateway, memory = make(tmp_path)
        try:
            await run_chat(ChatSession(gateway, memory), read=lambda _: next(inputs),
                           write=out.append)
        finally:
            await memory.aclose()
            await gateway.aclose()

    asyncio.run(go())
    assert any("usage: /delegate" in line for line in out)
