"""NVIDIA NIM / Nemotron adapter.

NVIDIA's hosted endpoint is OpenAI-compatible, so this is a thin
configuration of :class:`~warnetech_core.providers.openai.OpenAIProvider`
rather than a new wire implementation — same request shape, same response
shape, different base URL and key.

It exists as its own provider *name* (not just an `OpenAIProvider` with a
custom `base_url`) so that cost, rate limits and fallback are tracked against
NVIDIA separately from OpenAI. Two providers sharing one bucket would let a
Nemotron burst consume the OpenAI budget.

Warnetech's legacy Cloudflare worker (``worker/utils/nemotron.js`` in
claude-command-cli) calls this endpoint directly with a hardcoded model and
system prompt. Routing through the gateway instead is what gets that call
retry, fallback, prompt versioning and cost attribution.
"""

from __future__ import annotations

import os

import httpx

from ..errors import ConfigError
from .openai import OpenAIProvider

DEFAULT_BASE_URL = "https://integrate.api.nvidia.com/v1"


class NvidiaProvider(OpenAIProvider):
    """Talks to NVIDIA's OpenAI-compatible chat-completions endpoint."""

    name = "nvidia"

    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str | None = None,
        timeout: float = 60.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        key = api_key or os.getenv("NVIDIA_API_KEY")
        if not key and client is None:
            raise ConfigError("NVIDIA_API_KEY is not set and no api_key was passed")
        super().__init__(
            api_key=key,
            base_url=base_url or os.getenv("NVIDIA_BASE_URL") or DEFAULT_BASE_URL,
            timeout=timeout,
            client=client,
            name="nvidia",
        )
