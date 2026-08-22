"""The sync bridge — the seam Warnetech's synchronous components use."""

import asyncio

import pytest

from warnetech_core import AIGateway
from warnetech_core.integrations.ai_controller import make_batch_embed_fn, make_embed_fn
from warnetech_core.sync import SyncRunner, default_runner


async def double(x: int) -> int:
    await asyncio.sleep(0)
    return x * 2


def test_runs_a_coroutine_from_plain_sync_code():
    with SyncRunner() as runner:
        assert runner.run(double(21)) == 42


def test_runs_from_inside_a_running_loop():
    # This is the case asyncio.run() cannot handle, and the reason the bridge
    # uses a separate thread rather than nesting loops.
    with SyncRunner() as runner:
        async def caller():
            return runner.run(double(10))

        assert asyncio.run(caller()) == 20


def test_reuses_one_loop_across_calls():
    # httpx.AsyncClient pools are bound to a loop; a fresh loop per call would
    # break provider connection reuse.
    async def loop_id() -> int:
        return id(asyncio.get_running_loop())

    with SyncRunner() as runner:
        assert len({runner.run(loop_id()) for _ in range(3)}) == 1


def test_exceptions_propagate_to_the_caller():
    async def boom():
        raise ValueError("kaboom")

    with SyncRunner() as runner:
        with pytest.raises(ValueError, match="kaboom"):
            runner.run(boom())


def test_runner_restarts_after_close():
    runner = SyncRunner()
    assert runner.run(double(1)) == 2
    runner.close()
    assert runner.run(double(2)) == 4
    runner.close()


def test_closing_an_unstarted_runner_is_safe():
    SyncRunner().close()


def test_default_runner_is_shared():
    assert default_runner() is default_runner()


def test_timeout_is_enforced():
    async def slow():
        await asyncio.sleep(5)

    with SyncRunner() as runner:
        with pytest.raises(TimeoutError):
            runner.run(slow(), timeout=0.05)


# ---- warnetech_ai_controller bridge -------------------------------------

def test_embed_fn_matches_the_ai_controller_signature():
    gateway = AIGateway.offline()
    with SyncRunner() as runner:
        embed = make_embed_fn(gateway, project="firewall", runner=runner)
        vector = embed("SQL injection attempt on /login")
        assert isinstance(vector, list) and vector
        assert all(isinstance(v, float) for v in vector)


def test_embed_fn_is_deterministic_offline():
    gateway = AIGateway.offline()
    with SyncRunner() as runner:
        embed = make_embed_fn(gateway, runner=runner)
        assert embed("same text") == embed("same text")


def test_embed_fn_attributes_cost_to_its_own_project():
    gateway = AIGateway.offline()
    with SyncRunner() as runner:
        make_embed_fn(gateway, project="firewall", runner=runner)("x")
    row = gateway.usage.recent()[0]
    assert (row.project, row.operation) == ("firewall", "embed")


def test_batch_embed_fn_uses_one_call_for_many_texts():
    gateway = AIGateway.offline()
    with SyncRunner() as runner:
        vectors = make_batch_embed_fn(gateway, runner=runner)(["a", "b", "c"])
    assert len(vectors) == 3
    assert len([r for r in gateway.usage.recent() if r.operation == "embed"]) == 1


def test_batch_embed_fn_short_circuits_on_empty_input():
    gateway = AIGateway.offline()
    with SyncRunner() as runner:
        assert make_batch_embed_fn(gateway, runner=runner)([]) == []
    assert gateway.usage.recent() == []


def test_embed_fn_ranks_related_text_above_unrelated():
    # Mirrors warnetech_ai_controller.relevance.rank_by_similarity's usage.
    from warnetech_core.memory.types import cosine_similarity

    gateway = AIGateway.offline()
    with SyncRunner() as runner:
        embed = make_embed_fn(gateway, runner=runner)
        query = embed("sql injection login")
        related = cosine_similarity(query, embed("SQL injection detected on login endpoint"))
        unrelated = cosine_similarity(query, embed("disk usage nominal on node four"))
    assert related > unrelated
