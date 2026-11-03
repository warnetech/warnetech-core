"""Ollama adapter for local models.

Ollama serves an OpenAI-compatible API under ``/v1`` on the local host, so this
is a configuration of :class:`~warnetech_core.providers.openai.OpenAIProvider`
(the same pattern as :mod:`warnetech_core.providers.nvidia`). Requests still go
through the gateway, so retries, budgets and cost attribution apply to local
models too. Local inference costs nothing per token, so specs for Ollama models
should carry zero prices; the usage tracker still counts their tokens.

No API key is needed. Ollama ignores the ``Authorization`` header, so a fixed
placeholder is sent to satisfy the parent constructor. It is not a secret.
"""

from __future__ import annotations

import os

import httpx

from .openai import OpenAIProvider

DEFAULT_HOST = "http://localhost:11434"
_PLACEHOLDER_KEY = "ollama"  # not a credential; Ollama does not check it


def ollama_base_url(host: str | None = None) -> str:
    """Return the OpenAI-compatible base URL for an Ollama host.

    Accepts ``http://host:11434`` or the already-suffixed ``.../v1`` form.
    """
    raw = (host or os.getenv("OLLAMA_HOST") or DEFAULT_HOST).rstrip("/")
    if not raw.startswith(("http://", "https://")):
        raw = f"http://{raw}"
    return raw if raw.endswith("/v1") else f"{raw}/v1"


class OllamaProvider(OpenAIProvider):
    """Chat and embeddings against a local or LAN Ollama server."""

    name = "ollama"

    def __init__(
        self,
        *,
        host: str | None = None,
        timeout: float = 300.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        # Local models can be slow on first load, so the default timeout is
        # far longer than for hosted APIs.
        super().__init__(
            api_key=_PLACEHOLDER_KEY,
            base_url=ollama_base_url(host),
            timeout=timeout,
            client=client,
            name="ollama",
        )
