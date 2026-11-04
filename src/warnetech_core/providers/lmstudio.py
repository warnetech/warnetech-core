"""LM Studio adapter for local models.

LM Studio's local server speaks the OpenAI chat-completions protocol, by default
on port 1234, so this is a configuration of
:class:`~warnetech_core.providers.openai.OpenAIProvider` in the same way as the
Ollama and NVIDIA adapters. It needs no API key; the placeholder sent in the
``Authorization`` header is not a credential.
"""

from __future__ import annotations

import os

import httpx

from .openai import OpenAIProvider

DEFAULT_HOST = "http://localhost:1234"
_PLACEHOLDER_KEY = "lm-studio"  # not a credential; the local server does not check it


def lmstudio_base_url(host: str | None = None) -> str:
    """Return the OpenAI-compatible base URL for an LM Studio host."""
    raw = (host or os.getenv("LMSTUDIO_HOST") or DEFAULT_HOST).rstrip("/")
    if not raw.startswith(("http://", "https://")):
        raw = f"http://{raw}"
    return raw if raw.endswith("/v1") else f"{raw}/v1"


class LMStudioProvider(OpenAIProvider):
    name = "lmstudio"

    def __init__(
        self,
        *,
        host: str | None = None,
        timeout: float = 300.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(
            api_key=_PLACEHOLDER_KEY,
            base_url=lmstudio_base_url(host),
            timeout=timeout,
            client=client,
            name="lmstudio",
        )
