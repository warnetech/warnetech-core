"""Offline, deterministic provider.

Two jobs:

* let a mini-project run end-to-end with no API keys during local development;
* give the test suite a provider whose output is fully predictable, without
  monkeypatching HTTP.

The embeddings are a hashed bag-of-words projection. They are *not* semantically
meaningful — they only guarantee that identical text embeds identically and that
overlapping text scores higher than disjoint text, which is enough to exercise
the retrieval path.
"""

from __future__ import annotations

import hashlib
import math
import re
import time

from ..registry import ModelSpec
from ..types import (
    CompletionRequest,
    CompletionResponse,
    EmbeddingRequest,
    EmbeddingResponse,
    TokenUsage,
)
from .base import Provider

_WORD_RE = re.compile(r"\w+")


def estimate_tokens(text: str) -> int:
    """Rough token count: ~4 characters per token. Good enough for offline runs."""
    return max(1, len(text) // 4)


def hash_embedding(text: str, dimensions: int) -> list[float]:
    """Deterministic L2-normalised hashed bag-of-words vector."""
    vector = [0.0] * dimensions
    tokens = _WORD_RE.findall(text.lower())
    for token in tokens:
        digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
        index = int.from_bytes(digest[:4], "big") % dimensions
        # Sign from an independent byte so common tokens do not all push one way.
        sign = 1.0 if digest[4] % 2 == 0 else -1.0
        vector[index] += sign
    norm = math.sqrt(sum(v * v for v in vector))
    if norm == 0.0:
        # Empty/punctuation-only input: park it on a fixed unit vector rather
        # than returning zeros, which would make cosine similarity undefined.
        vector[0] = 1.0
        return vector
    return [v / norm for v in vector]


class EchoProvider(Provider):
    """Returns a deterministic transformation of the input instead of calling out."""

    name = "echo"

    def __init__(self, *, prefix: str = "echo:", latency_ms: float = 0.0) -> None:
        self.prefix = prefix
        self.latency_ms = latency_ms

    async def complete(self, request: CompletionRequest, spec: ModelSpec) -> CompletionResponse:
        started = time.perf_counter()
        last_user = next(
            (m.content for m in reversed(request.messages) if m.role == "user"),
            request.messages[-1].content,
        )
        text = f"{self.prefix}{last_user}"[: request.max_tokens * 4]
        prompt_chars = sum(len(m.content) for m in request.messages)
        usage = TokenUsage(
            input_tokens=estimate_tokens("x" * prompt_chars),
            output_tokens=estimate_tokens(text),
        )
        return CompletionResponse(
            text=text,
            model_alias=spec.alias,
            provider=self.name,
            provider_model=spec.provider_model,
            usage=usage,
            cost_usd=spec.cost_usd(usage.input_tokens, usage.output_tokens),
            latency_ms=(time.perf_counter() - started) * 1000 + self.latency_ms,
            finish_reason="stop",
        )

    async def embed(self, request: EmbeddingRequest, spec: ModelSpec) -> EmbeddingResponse:
        started = time.perf_counter()
        dims = spec.dimensions or 256
        vectors = [hash_embedding(text, dims) for text in request.inputs]
        usage = TokenUsage(input_tokens=sum(estimate_tokens(t) for t in request.inputs))
        return EmbeddingResponse(
            vectors=vectors,
            model_alias=spec.alias,
            provider=self.name,
            provider_model=spec.provider_model,
            usage=usage,
            cost_usd=spec.cost_usd(usage.input_tokens, 0),
            latency_ms=(time.perf_counter() - started) * 1000 + self.latency_ms,
            dimensions=dims,
        )
