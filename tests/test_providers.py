"""Provider adapters, driven against a mocked transport.

These prove the request/response translation without touching the network.
"""

import httpx
import pytest

from warnetech_core.errors import ProviderError, RateLimitError, TimeoutError_
from warnetech_core.providers.anthropic import AnthropicProvider
from warnetech_core.providers.echo import EchoProvider, hash_embedding
from warnetech_core.providers.openai import OpenAIProvider
from warnetech_core.registry import ModelRegistry, ModelSpec
from warnetech_core.types import CompletionRequest, EmbeddingRequest, Message


def mock_client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://test")


def request_for(**kwargs) -> CompletionRequest:
    base = {"model": "m", "messages": [Message.system("be terse"), Message.user("hi")]}
    return CompletionRequest(**{**base, **kwargs})


CHAT_SPEC = ModelSpec(
    alias="a", provider="anthropic", provider_model="claude-x",
    input_cost_per_mtok=3.0, output_cost_per_mtok=15.0,
)
OPENAI_SPEC = ModelSpec(
    alias="o", provider="openai", provider_model="gpt-x",
    input_cost_per_mtok=2.5, output_cost_per_mtok=10.0,
)
EMBED_SPEC = ModelSpec(
    alias="e", provider="openai", provider_model="embed-x",
    modality="embedding", input_cost_per_mtok=0.02, dimensions=3,
)


# ---- Anthropic ----------------------------------------------------------

async def test_anthropic_lifts_the_system_prompt_out_of_messages():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json
        seen.update(json.loads(request.content))
        return httpx.Response(200, json={
            "id": "msg_1", "stop_reason": "end_turn",
            "content": [{"type": "text", "text": "hello"}],
            "usage": {"input_tokens": 10, "output_tokens": 4},
        })

    provider = AnthropicProvider(client=mock_client(handler))
    response = await provider.complete(request_for(), CHAT_SPEC)

    assert seen["system"] == "be terse"
    assert [m["role"] for m in seen["messages"]] == ["user"]
    assert response.text == "hello"
    assert response.usage.input_tokens == 10
    assert response.cost_usd == pytest.approx(10 * 3.0 / 1e6 + 4 * 15.0 / 1e6)


async def test_anthropic_concatenates_multiple_text_blocks():
    def handler(request):
        return httpx.Response(200, json={
            "content": [{"type": "text", "text": "a"},
                        {"type": "thinking", "thinking": "ignored"},
                        {"type": "text", "text": "b"}],
            "usage": {"input_tokens": 1, "output_tokens": 1},
        })

    provider = AnthropicProvider(client=mock_client(handler))
    assert (await provider.complete(request_for(), CHAT_SPEC)).text == "ab"


async def test_max_tokens_is_clamped_to_the_model_ceiling():
    seen = {}

    def handler(request):
        import json
        seen.update(json.loads(request.content))
        return httpx.Response(200, json={"content": [{"type": "text", "text": "x"}], "usage": {}})

    spec = ModelSpec(alias="a", provider="anthropic", provider_model="m", max_output_tokens=100)
    provider = AnthropicProvider(client=mock_client(handler))
    await provider.complete(request_for(max_tokens=999_999), spec)
    assert seen["max_tokens"] == 100


# ---- OpenAI -------------------------------------------------------------

async def test_openai_keeps_the_system_message_inline():
    seen = {}

    def handler(request):
        import json
        seen.update(json.loads(request.content))
        return httpx.Response(200, json={
            "id": "c1",
            "choices": [{"message": {"content": "hey"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 8, "completion_tokens": 2},
        })

    provider = OpenAIProvider(client=mock_client(handler))
    response = await provider.complete(request_for(), OPENAI_SPEC)
    assert [m["role"] for m in seen["messages"]] == ["system", "user"]
    assert (response.text, response.finish_reason) == ("hey", "stop")


async def test_openai_embeddings_are_returned_in_input_order():
    def handler(request):
        # Deliberately out of order; the adapter must sort on `index`.
        return httpx.Response(200, json={
            "data": [
                {"index": 1, "embedding": [0.0, 1.0, 0.0]},
                {"index": 0, "embedding": [1.0, 0.0, 0.0]},
            ],
            "usage": {"prompt_tokens": 4},
        })

    provider = OpenAIProvider(client=mock_client(handler))
    response = await provider.embed(
        EmbeddingRequest(model="e", inputs=["first", "second"]), EMBED_SPEC
    )
    assert response.vectors == [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]
    assert response.dimensions == 3


# ---- error normalisation ------------------------------------------------

@pytest.mark.parametrize("status,retryable", [(500, True), (503, True), (429, True),
                                              (400, False), (401, False), (404, False)])
async def test_http_errors_carry_the_right_retryability(status, retryable):
    def handler(request):
        return httpx.Response(status, json={"error": {"message": "nope"}})

    provider = OpenAIProvider(client=mock_client(handler))
    with pytest.raises(ProviderError) as excinfo:
        await provider.complete(request_for(), OPENAI_SPEC)
    assert excinfo.value.retryable is retryable
    assert "nope" in str(excinfo.value)


async def test_rate_limit_captures_retry_after():
    def handler(request):
        return httpx.Response(429, headers={"retry-after": "2.5"},
                              json={"error": {"message": "slow down"}})

    provider = OpenAIProvider(client=mock_client(handler))
    with pytest.raises(RateLimitError) as excinfo:
        await provider.complete(request_for(), OPENAI_SPEC)
    assert excinfo.value.retry_after == 2.5


async def test_timeouts_are_normalised_and_retryable():
    def handler(request):
        raise httpx.ConnectTimeout("too slow")

    provider = OpenAIProvider(client=mock_client(handler))
    with pytest.raises(TimeoutError_) as excinfo:
        await provider.complete(request_for(), OPENAI_SPEC)
    assert excinfo.value.retryable is True


async def test_non_json_body_is_reported_clearly():
    def handler(request):
        return httpx.Response(200, text="<html>gateway error</html>")

    provider = OpenAIProvider(client=mock_client(handler))
    with pytest.raises(ProviderError, match="non-JSON"):
        await provider.complete(request_for(), OPENAI_SPEC)


async def test_missing_choices_is_reported_not_indexed_blindly():
    def handler(request):
        return httpx.Response(200, json={"id": "c1"})

    provider = OpenAIProvider(client=mock_client(handler))
    with pytest.raises(ProviderError, match="no `choices`"):
        await provider.complete(request_for(), OPENAI_SPEC)


# ---- echo / offline -----------------------------------------------------

async def test_echo_returns_the_last_user_turn():
    registry = ModelRegistry.default()
    response = await EchoProvider().complete(
        request_for(messages=[Message.user("a"), Message.assistant("b"), Message.user("c")]),
        registry.resolve("echo.chat"),
    )
    assert response.text == "echo:c"


def test_hash_embeddings_are_deterministic_and_normalised():
    first, second = hash_embedding("the cat sat", 64), hash_embedding("the cat sat", 64)
    assert first == second
    assert sum(v * v for v in first) == pytest.approx(1.0)


def test_hash_embeddings_separate_unrelated_text():
    from warnetech_core.memory.types import cosine_similarity

    same = cosine_similarity(hash_embedding("alpha beta", 256), hash_embedding("alpha beta", 256))
    different = cosine_similarity(
        hash_embedding("alpha beta", 256), hash_embedding("zulu yankee", 256)
    )
    assert same > different


def test_empty_text_still_yields_a_unit_vector():
    vector = hash_embedding("   ", 16)
    assert sum(v * v for v in vector) == pytest.approx(1.0)
