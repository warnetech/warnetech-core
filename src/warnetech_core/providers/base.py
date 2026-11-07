"""Provider adapter interface.

A provider translates our neutral :class:`CompletionRequest` into whatever the
upstream API wants, and translates the response back. Adapters do *not* retry,
rate-limit, track cost or log — the gateway owns all of that, once, for every
provider.
"""

from __future__ import annotations

import abc

from ..registry import ModelSpec
from ..types import CompletionRequest, CompletionResponse, EmbeddingRequest, EmbeddingResponse


class Provider(abc.ABC):
    """Base class for every model provider adapter."""

    #: Registry key. Must match ``ModelSpec.provider``.
    name: str = "unset"

    @abc.abstractmethod
    async def complete(
        self, request: CompletionRequest, spec: ModelSpec
    ) -> CompletionResponse:
        """Run one chat completion. Raise :class:`ProviderError` on failure."""

    async def embed(self, request: EmbeddingRequest, spec: ModelSpec) -> EmbeddingResponse:
        """Embed a batch of strings. Optional — not every provider supports it."""
        raise NotImplementedError(f"{self.name} does not support embeddings")

    async def aclose(self) -> None:
        """Release any held resources (HTTP pools, sockets)."""
        return None
