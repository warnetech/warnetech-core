"""llama.cpp adapter for local models.

``llama-server`` (from llama.cpp) serves the OpenAI chat-completions protocol, by default
on port 8080, so this is a configuration of
:class:`~warnetech_core.providers.openai.OpenAIProvider`, like LM Studio and Ollama. It
needs no API key; the placeholder sent in ``Authorization`` is not a credential.
"""

from __future__ import annotations

import os

import httpx

from .openai import OpenAIProvider

DEFAULT_HOST = "http://localhost:8080"
_PLACEHOLDER_KEY = "llama.cpp"  # not a credential; the local server does not check it


def llamacpp_base_url(host: str | None = None) -> str:
    """Return the OpenAI-compatible base URL for a llama-server host."""
    raw = (host or os.getenv("LLAMACPP_HOST") or DEFAULT_HOST).rstrip("/")
    if not raw.startswith(("http://", "https://")):
        raw = f"http://{raw}"
    return raw if raw.endswith("/v1") else f"{raw}/v1"


class LlamaCppProvider(OpenAIProvider):
    name = "llamacpp"

    def __init__(
        self,
        *,
        host: str | None = None,
        timeout: float = 300.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(
            api_key=_PLACEHOLDER_KEY,
            base_url=llamacpp_base_url(host),
            timeout=timeout,
            client=client,
            name="llamacpp",
        )
