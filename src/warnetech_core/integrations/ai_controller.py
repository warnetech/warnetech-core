"""Bridge to ``warnetech_ai_controller``.

The AI controller in claude-command-cli already generates embeddings and ranks
slices, but its default embedder is a SHA-256 projection of the whole
serialised payload — documented there as a "deterministic, dependency-free
fallback". That only supports exact and near-duplicate recall: two payloads
differing by one byte hash to unrelated vectors, so semantic similarity is not
available at all.

Every function in that module accepts an ``embed_fn`` for exactly this reason:

    EmbedFn = Callable[[str], list[float]]

:func:`make_embed_fn` returns one backed by the gateway, which means real
embeddings *plus* the retry, fallback, rate limiting and per-project cost
attribution that the rest of warnetech_core provides. Nothing in the AI
controller changes:

    from warnetech_core import AIGateway
    from warnetech_core.integrations import make_embed_fn

    gateway = AIGateway.from_env()
    embed = make_embed_fn(gateway, project="warnetech-firewall")

    generate_slice_embedding(slice_metadata, embed_fn=embed)
    rank_by_similarity(embed(query_text), slice_embeddings)

Note the dimensionality difference: ``AIControllerConfig.EmbeddingSettings``
defaults to 768, while ``embed.default`` (openai.text-embedding-3-small)
produces 1536. Cosine similarity requires equal dimensions, so a corpus must
be embedded consistently — pass a matching ``EmbeddingSettings(dimensions=...)``
when mixing, and re-embed rather than comparing across models.
"""

from __future__ import annotations

from collections.abc import Callable

from ..gateway import AIGateway
from ..sync import SyncRunner, default_runner

#: Matches ``warnetech_ai_controller.embeddings.EmbedFn``.
EmbedFn = Callable[[str], list[float]]


def make_embed_fn(
    gateway: AIGateway,
    *,
    project: str = "warnetech-ai-controller",
    team: str | None = None,
    model: str = "embed.default",
    runner: SyncRunner | None = None,
    timeout: float | None = 60.0,
) -> EmbedFn:
    """Build a synchronous ``embed_fn`` backed by the gateway.

    Args:
        project: Cost attribution for embeddings generated through this
            callable. Give the AI controller its own project so its embedding
            spend is separable from application traffic.
        model: Registry alias to embed with.
        runner: Event loop to run on. Defaults to the process-wide runner;
            pass your own to control shutdown.
        timeout: Seconds to wait per call. ``None`` waits indefinitely.

    Returns:
        A callable taking one string and returning one vector.
    """
    active = runner or default_runner()

    def embed_fn(text: str) -> list[float]:
        response = active.run(
            gateway.embed(model, [text], project=project, team=team), timeout=timeout
        )
        return response.vectors[0]

    return embed_fn


def make_batch_embed_fn(
    gateway: AIGateway,
    *,
    project: str = "warnetech-ai-controller",
    team: str | None = None,
    model: str = "embed.default",
    runner: SyncRunner | None = None,
    timeout: float | None = 120.0,
) -> Callable[[list[str]], list[list[float]]]:
    """Batch variant — one provider call for N texts instead of N calls.

    Prefer this when embedding a whole slice set: the per-request overhead and
    the rate-limit budget are both charged per call, not per text.
    """
    active = runner or default_runner()

    def batch_embed_fn(texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        response = active.run(
            gateway.embed(model, texts, project=project, team=team), timeout=timeout
        )
        return response.vectors

    return batch_embed_fn
