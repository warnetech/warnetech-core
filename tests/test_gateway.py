import pytest

from warnetech_core import AIGateway, Message
from warnetech_core.errors import AllProvidersFailed, ConfigError, ProviderError
from warnetech_core.providers.base import Provider
from warnetech_core.providers.echo import EchoProvider
from warnetech_core.registry import ModelRegistry, ModelSpec
from warnetech_core.resilience import RetryPolicy
from warnetech_core.usage import BudgetExceeded, InMemoryUsageStore, UsageTracker


class ScriptedProvider(Provider):
    """Fails its first `fail_times` calls, then delegates to echo."""

    name = "scripted"

    def __init__(self, fail_times: int, *, status: int = 500, retryable: bool = True):
        self.calls = 0
        self.fail_times = fail_times
        self.status = status
        self.retryable = retryable

    async def complete(self, request, spec):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise ProviderError(
                "scripted failure", provider=self.name,
                status_code=self.status, retryable=self.retryable,
            )
        return await EchoProvider().complete(request, spec)


def build(fail_times: int, **kwargs):
    registry = ModelRegistry(specs=[
        ModelSpec(alias="primary", provider="scripted", provider_model="x", fallbacks=("backup",)),
        ModelSpec(alias="backup", provider="echo", provider_model="echo-chat"),
    ])
    provider = ScriptedProvider(fail_times, **kwargs)
    gateway = AIGateway(
        {"scripted": provider, "echo": EchoProvider()},
        registry=registry,
        usage=UsageTracker(InMemoryUsageStore()),
        retry=RetryPolicy(max_attempts=3, initial_delay=0.001, max_delay=0.002, jitter=False),
    )
    return gateway, provider


async def test_completion_returns_normalised_response(gateway):
    response = await gateway.complete("chat.default", "hello", project="p")
    assert response.text == "echo:hello"
    assert (response.provider, response.attempts) == ("echo", 1)


async def test_string_prompt_becomes_a_user_message(gateway):
    response = await gateway.complete("chat.default", "hi")
    assert response.text == "echo:hi"


async def test_system_prompt_is_prepended(gateway):
    response = await gateway.complete("chat.default", [Message.user("q")], system="be terse")
    # Echo returns the last *user* turn, so the system prompt must not have displaced it.
    assert response.text == "echo:q"


async def test_transient_failures_are_retried_on_the_same_model():
    gateway, provider = build(fail_times=2)
    response = await gateway.complete("primary", "hi")
    assert (provider.calls, response.attempts, response.fell_back_from) == (3, 3, [])


async def test_exhausted_retries_fall_back_to_another_provider():
    gateway, provider = build(fail_times=99)
    response = await gateway.complete("primary", "hi")
    assert response.model_alias == "backup"
    assert response.fell_back_from == ["primary"]
    assert provider.calls == 3  # retried to exhaustion before moving on


async def test_client_errors_skip_retry_and_fallback():
    # A 400 means our payload is wrong; the next provider will reject it too.
    gateway, provider = build(fail_times=99, status=400, retryable=False)
    with pytest.raises(AllProvidersFailed) as excinfo:
        await gateway.complete("primary", "hi")
    assert provider.calls == 1
    assert [alias for alias, _ in excinfo.value.failures] == ["primary"]


async def test_fallback_can_be_disabled():
    gateway, _ = build(fail_times=99)
    gateway.enable_fallback = False
    with pytest.raises(AllProvidersFailed) as excinfo:
        await gateway.complete("primary", "hi")
    assert [alias for alias, _ in excinfo.value.failures] == ["primary"]


async def test_unusable_fallbacks_are_dropped_from_the_chain():
    registry = ModelRegistry(specs=[
        ModelSpec(alias="primary", provider="echo", provider_model="echo-chat",
                  fallbacks=("ghost",)),
        ModelSpec(alias="ghost", provider="not-registered", provider_model="x"),
    ])
    gateway = AIGateway({"echo": EchoProvider()}, registry=registry)
    assert [s.alias for s in gateway._chain("primary", modality="chat")] == ["primary"]


async def test_requesting_a_chat_model_for_embeddings_is_rejected(gateway):
    with pytest.raises(ConfigError, match="chat model"):
        await gateway.embed("chat.default", ["x"])


async def test_every_call_is_attributed_to_a_project_and_team(gateway):
    await gateway.complete("chat.default", "a", project="hub", team="core")
    row = gateway.usage.recent()[0]
    assert (row.project, row.team, row.operation) == ("hub", "core", "complete")


async def test_failed_calls_are_still_recorded():
    gateway, _ = build(fail_times=99)
    await gateway.complete("primary", "hi")
    rows = {r.model_alias: r for r in gateway.usage.recent()}
    assert rows["primary"].ok is False and rows["primary"].attempts == 3
    assert rows["backup"].ok is True


async def test_prompt_calls_carry_prompt_attribution(gateway):
    await gateway.complete_prompt("greet", {"name": "Ada"}, project="hub")
    row = gateway.usage.recent()[0]
    assert (row.prompt_id, row.prompt_version) == ("greet", 2)
    assert "prompt_checksum" in row.metadata


async def test_prompt_version_can_be_pinned(gateway):
    response = await gateway.complete_prompt("greet", {"name": "Ada"}, version=1)
    assert "Say hello to Ada" in response.text
    assert gateway.usage.recent()[0].prompt_version == 1


async def test_prompt_without_a_model_must_be_given_one():
    gateway = AIGateway.offline()
    gateway.prompts.add("bare", [("user", "hi")], version=1)
    with pytest.raises(ConfigError, match="declares no `model`"):
        await gateway.complete_prompt("bare")


async def test_budget_is_enforced_before_dispatch():
    tracker = UsageTracker(InMemoryUsageStore(), project_budgets_usd={"hub": 0.0001})
    registry = ModelRegistry.default().use_offline_models()
    registry.register(ModelSpec(
        alias="echo.chat", provider="echo", provider_model="echo-chat",
        input_cost_per_mtok=1000.0, output_cost_per_mtok=1000.0,
    ))
    gateway = AIGateway({"echo": EchoProvider()}, registry=registry, usage=tracker)
    await gateway.complete("chat.default", "x" * 4000, project="hub")
    with pytest.raises(BudgetExceeded, match="hub"):
        await gateway.complete("chat.default", "again", project="hub")
    # A different project is unaffected.
    await gateway.complete("chat.default", "fine", project="other")


async def test_embeddings_are_batched_into_one_call(gateway):
    response = await gateway.embed("embed.default", ["a", "b", "c"], project="p")
    assert len(response.vectors) == 3
    assert len([r for r in gateway.usage.recent() if r.operation == "embed"]) == 1


async def test_gateway_closes_its_providers():
    closed = []

    class Closing(EchoProvider):
        name = "echo"

        async def aclose(self):
            closed.append(True)

    async with AIGateway({"echo": Closing()}) as gateway:
        assert gateway.providers
    assert closed == [True]
