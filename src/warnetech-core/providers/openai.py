"""OpenAI chat-completions and embeddings adapter."""

from __future__ import annotations

import os
import time
from typing import Any

import httpx

from ..errors import ConfigError, ProviderError
from ..registry import ModelSpec
from ..types import (
    CompletionRequest,
    CompletionResponse,
    EmbeddingRequest,
    EmbeddingResponse,
    TokenUsage,
)
from .base import Provider
from .http import HTTPProviderMixin

DEFAULT_BASE_URL = "https://api.openai.com/v1"


class OpenAIProvider(HTTPProviderMixin, Provider):
    """Talks to ``/chat/completions`` and ``/embeddings``.

    The base URL is configurable, so any OpenAI-compatible endpoint (Together,
    Groq, a local vLLM) can be registered as a separate provider instance
    without a new adapter.
    """

    name = "openai"

    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str | None = None,
        timeout: float = 60.0,
        organization: str | None = None,
        client: httpx.AsyncClient | None = None,
        name: str | None = None,
    ) -> None:
        key = api_key or os.getenv("OPENAI_API_KEY")
        if not key and client is None:
            raise ConfigError("OPENAI_API_KEY is not set and no api_key was passed")
        headers = {"authorization": f"Bearer {key or ''}", "content-type": "application/json"}
        org = organization or os.getenv("OPENAI_ORG_ID")
        if org:
            headers["openai-organization"] = org
        super().__init__(
            base_url=base_url or os.getenv("OPENAI_BASE_URL") or DEFAULT_BASE_URL,
            headers=headers,
            timeout=timeout,
            client=client,
        )
        if name:
            self.name = name

    async def complete(self, request: CompletionRequest, spec: ModelSpec) -> CompletionResponse:
        payload: dict[str, Any] = {
            "model": spec.provider_model,
            "messages": [m.model_dump() for m in request.messages],
            "max_tokens": min(request.max_tokens, spec.max_output_tokens),
            "temperature": request.temperature,
        }
        if request.stop:
            payload["stop"] = request.stop

        started = time.perf_counter()
        body = await self.post_json("/chat/completions", payload)
        latency_ms = (time.perf_counter() - started) * 1000

        choices = body.get("choices")
        if not choices:
            raise ProviderError("response had no `choices`", provider=self.name)
        choice = choices[0]
        text = (choice.get("message") or {}).get("content") or ""

        raw_usage = body.get("usage") or {}
        usage = TokenUsage(
            input_tokens=int(raw_usage.get("prompt_tokens") or 0),
            output_tokens=int(raw_usage.get("completion_tokens") or 0),
        )
        return CompletionResponse(
            id=str(body.get("id") or ""),
            text=text,
            model_alias=spec.alias,
            provider=self.name,
            provider_model=spec.provider_model,
            usage=usage,
            cost_usd=spec.cost_usd(usage.input_tokens, usage.output_tokens),
            latency_ms=latency_ms,
            finish_reason=choice.get("finish_reason"),
            raw=body,
        )

    async def embed(self, request: EmbeddingRequest, spec: ModelSpec) -> EmbeddingResponse:
        payload: dict[str, Any] = {"model": spec.provider_model, "input": request.inputs}
        if spec.dimensions:
            payload["dimensions"] = spec.dimensions

        started = time.perf_counter()
        body = await self.post_json("/embeddings", payload)
        latency_ms = (time.perf_counter() - started) * 1000

        data = body.get("data")
        if not isinstance(data, list):
            raise ProviderError("embedding response had no `data`", provider=self.name)
        # The API is documented to preserve input order, but it also returns an
        # explicit index; sorting on it costs nothing and removes the assumption.
        ordered = sorted(data, key=lambda row: row.get("index", 0))
        vectors = [[float(x) for x in row["embedding"]] for row in ordered]

        raw_usage = body.get("usage") or {}
        usage = TokenUsage(input_tokens=int(raw_usage.get("prompt_tokens") or 0))
        return EmbeddingResponse(
            vectors=vectors,
            model_alias=spec.alias,
            provider=self.name,
            provider_model=spec.provider_model,
            usage=usage,
            cost_usd=spec.cost_usd(usage.input_tokens, 0),
            latency_ms=latency_ms,
            dimensions=len(vectors[0]) if vectors else 0,
        )
