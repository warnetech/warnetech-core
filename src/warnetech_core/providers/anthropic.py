"""Anthropic Messages API adapter."""

from __future__ import annotations

import os
import time
from typing import Any

import httpx

from ..errors import ConfigError, ProviderError
from ..registry import ModelSpec
from ..types import CompletionRequest, CompletionResponse, TokenUsage
from .base import Provider
from .http import HTTPProviderMixin

DEFAULT_BASE_URL = "https://api.anthropic.com"
API_VERSION = "2023-06-01"


class AnthropicProvider(HTTPProviderMixin, Provider):
    """Talks to ``/v1/messages``.

    Anthropic takes the system prompt as a top-level field rather than a turn in
    ``messages``, so we split it out of the neutral request here.
    """

    name = "anthropic"

    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str | None = None,
        timeout: float = 60.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        key = api_key or os.getenv("ANTHROPIC_API_KEY")
        if not key and client is None:
            raise ConfigError("ANTHROPIC_API_KEY is not set and no api_key was passed")
        super().__init__(
            base_url=base_url or os.getenv("ANTHROPIC_BASE_URL") or DEFAULT_BASE_URL,
            headers={
                "x-api-key": key or "",
                "anthropic-version": API_VERSION,
                "content-type": "application/json",
            },
            timeout=timeout,
            client=client,
        )

    async def complete(self, request: CompletionRequest, spec: ModelSpec) -> CompletionResponse:
        payload: dict[str, Any] = {
            "model": spec.provider_model,
            "max_tokens": min(request.max_tokens, spec.max_output_tokens),
            "temperature": request.temperature,
            "messages": [m.model_dump() for m in request.conversation()],
        }
        system = request.system_prompt()
        if system:
            payload["system"] = system
        if request.stop:
            payload["stop_sequences"] = request.stop

        started = time.perf_counter()
        body = await self.post_json("/v1/messages", payload)
        latency_ms = (time.perf_counter() - started) * 1000

        text = self._join_text(body)
        raw_usage = body.get("usage") or {}
        usage = TokenUsage(
            input_tokens=int(raw_usage.get("input_tokens") or 0),
            output_tokens=int(raw_usage.get("output_tokens") or 0),
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
            finish_reason=body.get("stop_reason"),
            raw=body,
        )

    @staticmethod
    def _join_text(body: dict[str, Any]) -> str:
        blocks = body.get("content")
        if not isinstance(blocks, list):
            raise ProviderError("response had no `content` array", provider="anthropic")
        return "".join(
            block.get("text", "")
            for block in blocks
            if isinstance(block, dict) and block.get("type") == "text"
        )
